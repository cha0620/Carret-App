"""변환 파이프라인 - LangGraph 판.

스테이지:
  0) load       : 원본 로드
  0.5) classify : 물건 식별 + 루브릭(considered) 수립
  1) read_text ∥ detect : 원본 물건 위 글자 / 하자 의미 앵커 (병렬, detect 실패는 detect_failed)
  1.5) plan     : 생성해도 못 지킬 게 보이면(detect 실패·잔글씨 多) 바로 배경 교체로
  2) generate   : 배경 교체 (생성)
  2.3) validate_result : ① 출력 가드(guards.py, OCR 글자 비교 포함) — hard fail 이면
       seed 만 바꿔 1회 재시도, 그래도 fail 이면 배경 교체(composite), 그것도 실패면
       status="blocked"(원본을 내보냄). ② 구도/자막 검사 — invalid면
       max_generate_attempts 까지 재생성
  2.5) score_similarity : 원본 vs 결과 DINOv2 코사인 유사도 (가드 값 재사용)
  3) verify     : 결과 → 하자·글자 보존 여부 + 좌표. 실패 → 1회 재생성 → composite
  4) finalize   : bubbles + 로깅
  (그래프 밖) judge_and_save : 품질 성적표 — 그래프가 끝난 뒤 한 곳에서만 채점.
       transform 라우트는 응답 뒤 백그라운드, 그 외(run_transform 기본값 ·
       run_transform_with_result)는 그래프 직후 바로
"""

import contextvars
import json
import logging
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.core.tracing import current_trace_id, flush, observe, score
from app.core.vlm import retryable
from app.prompts.presets import (TEXT_LOCK_MAX_CHARS, get_preset, prompt_safe,
                                 text_lock, text_where)
from app.prompts.rubric import AXES
from app.services.ai import compositor, detector, embedder, judge
from app.services.ai.generator import _generate_ai
from app.services.persistence import storage, store
from app.services.quality import guards

logger = logging.getLogger("carret.pipeline")


class State(TypedDict, total=False):
    file_id: str
    preset_key: str
    preset: dict
    original: bytes
    item: str                 # ⭐ classify 산출
    considered: list          # ⭐ classify 산출
    anchors: list
    detect_failed: bool        # detect 가 재시도까지 실패 — "하자 없음"과 구분 (검증 불가)
    verify_failed: bool        # verify 호출이 재시도까지 실패 — "보존됨"과 구분 (검증 불가)
    item_texts: list           # 원본 물건 위 글자 [{text, x1..}] → generate 프롬프트
    item_box: dict | None      # 원본에서 물건 위치 (0-1000) — 배경 교체 모드의 오리기 범위
    gate_retried: bool         # verify 게이트 실패로 재생성을 이미 1회 했나
    gate_note: str             # 그 재생성 때 프롬프트에 붙인 "사라진 하자" 목록
    mode: str                  # "generate" | "composite"(원본 물건 + 배경만 교체) | "composite_failed"
    composite_reason: str | None   # 배경 교체로 간 이유: detect_failed | text_heavy | many_defects
                                   #   | guard_failed | gate_failed | verify_failed
    guard_failed: bool         # 출력 가드가 seed 재시도까지 hard fail
    provided_result: bytes     # dev 그래프: generate 대신 쓸 결과 이미지
    composite_error: str | None
    result: bytes
    result_name: str
    gen_attempts: int          # ⭐ generate 시도 횟수 (재생성 게이트용)
    photo_check: dict          # ⭐ {"valid": bool, "reason": str} - 구도/자막 검사
    guard_seed: int | None     # 출력 가드 재시도에 쓴 seed (None = 재시도 안 함 = 기회 남음)
    guard_retry: bool          # validate_result → generate: 이번 generate 가 가드 재시도
    status: str                # "pass" | "blocked"
    guard_report: list         # 실패한 가드 목록 (hard/soft) - asdict(GuardResult)
    visual_similarity: float | None   # ⭐ DINOv2 코사인 유사도
    checks: list
    gate_passed: bool | None
    bubbles: list
    prompt_used: str


# ── 노드 ─────────────────────────────────────────
def load(s: State) -> dict:
    original = storage.load_original(s["file_id"])
    if original is None:
        raise FileNotFoundError(s["file_id"])
    return {
        "original": original,
        "preset": get_preset(s["preset_key"]),
    }


def classify_node(s: State) -> dict:
    """물건 식별 + 루브릭 수립 (실패 시 개방형 폴백)."""
    return detector.classify(s["original"])


def read_text(s: State) -> dict:
    """원본 물건 위 글자 읽기 — generate 프롬프트에 넣어 글자 뭉개짐을 줄인다.
    관측 단계와 같은 실패 정책: 실패하면 글자 없이 그대로 생성한다."""
    if not settings.text_lock:
        return {"item_texts": [], "item_box": None}
    try:
        out = detector.read_item_text(s["original"], s.get("item", "object"))
    except Exception as e:
        print(f"[read_text] 실패(무시): {e}")
        return {"item_texts": [], "item_box": None}
    return {"item_texts": out["texts"], "item_box": out.get("item_box")}


DETECT_ATTEMPTS = 2
DETECT_RETRY_DELAY_S = 1.0   # 429/일시 장애가 바로 또 나지 않게 잠깐 쉰다 (verify 도 같이 씀)
VERIFY_ATTEMPTS = 2


def detect(s: State) -> dict:
    """실패는 "하자 없음"과 다르다 — 빈 앵커로 넘기면 verify 가 검사할 게 없다며
    게이트를 통과시킨다. 재시도까지 실패하면 detect_failed 로 남겨 verify/라우팅이
    "검증 불가"로 다루게 한다 (생성 결과를 확인 없이 내보내지 않음)."""
    for attempt in range(1, DETECT_ATTEMPTS + 1):
        try:
            anchors = detector.detect_defects(
                s["original"], s.get("item", "object"), s.get("considered", []),
                strict=True)
            return {"anchors": anchors, "detect_failed": False}
        except Exception as e:
            print(f"[detect] 실패 ({attempt}/{DETECT_ATTEMPTS}): {e}")
            if not retryable(e):
                break
            if attempt < DETECT_ATTEMPTS:
                time.sleep(DETECT_RETRY_DELAY_S)
    return {"anchors": [], "detect_failed": True}


def _result_name(s: State) -> str:
    return f"{s['file_id']}_{s['preset_key']}.jpg"


def _composite_first_reason(s: State) -> str | None:
    """생성 전에 이미 "생성하면 못 지킨다"가 보이는 경우 — FLUX·재생성·verify 비용을
    쓰지 않고 바로 원본 픽셀을 쓰는 배경 교체로 보낸다."""
    if s.get("detect_failed"):
        return "detect_failed"     # 생성해도 확인할 기준(원본 하자 목록)이 없다
    n = settings.composite_first_min_texts
    if n and len(s.get("item_texts") or []) >= n:
        return "text_heavy"        # 잔글씨 많은 물건 — TEXT_LOCK 이 있어도 뭉개지기 쉽다
    n = settings.composite_first_min_anchors
    if n and len(s.get("anchors") or []) >= n:
        return "many_defects"
    return None


def plan(s: State) -> dict:
    """read_text ∥ detect 합류 지점. result_name 을 여기서 정해 두면 generate 를
    건너뛰는 경로(바로 composite)에서도 저장 이름이 있다."""
    out = {"result_name": _result_name(s)}
    if s.get("provided_result") is not None:
        return out   # dev 그래프 — 배경 교체로 가지 않는다 (inspect 에 엉뚱한 사유가 남지 않게)
    reason = _composite_first_reason(s)
    if reason:
        print(f"[plan] 생성 전 배경 교체 모드로: {reason}")
        out["composite_reason"] = reason
    return out


def _route_after_plan(s: State) -> str:
    """오리기마저 실패하면 composite 가 mode 를 generate 로 되돌려 그때 생성한다."""
    return "composite" if s.get("composite_reason") else "generate"


def generate(s: State) -> dict:
    preset = s["preset"]
    lock = text_lock(s.get("item_texts") or [])
    if lock:
        preset = {**preset, "prompt": preset["prompt"] + lock}
    if s.get("gate_note"):
        preset = {**preset, "prompt": preset["prompt"] + s["gate_note"]}
    prior_check = s.get("photo_check")
    # 가드 재시도는 "동일 파라미터 + seed 만 변경" — prior_check 가 그대로라
    # 아래 반려 사유 문구도 직전 시도와 똑같이 붙는다.
    if prior_check and not prior_check.get("valid", True):
        # 재시도 — 직전 반려 사유를 프롬프트에 붙여서 "맹목적" 재시도가 되지
        # 않게 한다 (같은 프롬프트를 그대로 다시 던지면 같은 결함이 반복되기 쉬움).
        note = prior_check.get("reason") or "구도가 잘렸거나 텍스트가 상품을 가림"
        preset = {**preset, "prompt": preset["prompt"] + (
            f"\n\nIMPORTANT: a previous attempt was rejected for this reason: "
            f"\"{note}\". Show the FULL item in frame (no cropping/zoom) and do "
            f"NOT add any caption, subtitle, watermark, or overlaid text.")}
    # seed 는 가드 재시도 그 1회에만 쓴다 — 이후 photo_check 재생성까지 같은
    # seed 로 고정되면 변주가 줄어 같은 잘림이 반복되기 쉽다.
    guard_retry = s.get("guard_retry", False)
    gen = (_generate_ai(s["original"], preset, seed=s["guard_seed"]) if guard_retry
           else _generate_ai(s["original"], preset))
    result_name = _result_name(s)
    # 저장은 validate_result 가 가드 통과 후에 한다 — 여기서 저장하면 가드 전
    # 이미지가 /storage/result/ 의 예측 가능한 URL 에 노출된다 (재시도 중이거나
    # 가드/재시도 호출이 예외로 끝난 경우 그대로 남음).
    return {
        "result": gen,
        "result_name": result_name,
        "prompt_used": preset["prompt"],
        # 가드 재시도는 photo_check 재생성 예산(max_generate_attempts)과 별개의
        # 1회 — 카운트하지 않아야 서로의 예산을 잡아먹지 않는다.
        "gen_attempts": s.get("gen_attempts", 0) + (0 if guard_retry else 1),
        "guard_retry": False,
        "visual_similarity": None,   # 새 이미지 — 이전 결과 기준 값은 무효
    }


def _guard_anchors(anchors: list) -> list:
    """run_output_guards 는 box 가 있는 앵커만 다룬다 — detect 앵커(what/where)는
    아직 box 가 없어서 지금은 전부 걸러지고 ocr/dino 가드만 돈다."""
    return [a for a in anchors if "box" in a]


def _ocr_pair(s: State) -> tuple[list, list, list]:
    """OCR 가드 입력 (원본 글자, 결과 글자, 추가 가드 결과).

    원본 글자는 read_text 가 이미 읽어 둔 것 중 _key_texts 로 고른 것, 결과 글자는
    같은 함수로 결과 이미지를 한 번 더 읽은 전부 (생성 1회당 VLM 1회). 원본에 글자가 없으면(또는
    TEXT_LOCK 꺼짐) 읽지 않는다 — 빈 목록끼리는 통과.
    결과 읽기 실패는 fail-open 하지 않는다 (guards.py 원칙: 최소선을 못 재면 통과 아님)."""
    # verify 와 같은 기준으로 고른 글자만 비교 — 잔글씨·잘린 문장까지 넣으면 두 번의
    # VLM 읽기 오차만으로 recall 이 떨어져 hard fail(오차단)이 난다.
    before = [text for text, _ in _key_texts(s.get("item_texts"))]
    if not before:
        return [], [], []
    try:
        out = detector.read_item_text(s["result"], s.get("item", "object"), strict=True)
    except Exception as e:
        print(f"[guards] 결과 글자 읽기 실패 → hard fail: {e}")
        return [], [], [guards.GuardResult(name="ocr_read_failed", passed=False,
                                           value=0.0, threshold=1.0, severity="hard")]
    return before, [t["text"] for t in out["texts"]], []


def _run_guards(s: State) -> tuple[str, list, float | None]:
    """(판정, 실패한 가드 목록, DINOv2 유사도). 가드 계산 예외는 삼키지 않는다
    (guards.py 설계: 최소선 gate 가 fail-open 되면 안 됨).
    유사도는 dino_band 가드가 이미 계산한 값 — score_similarity 가 다시 안 돌게 넘긴다."""
    before, after, extra = _ocr_pair(s)
    results = guards.run_output_guards(
        s["original"], s["result"], _guard_anchors(s.get("anchors", [])),
        before, after) + extra
    verdict, _ = guards.decide(results)
    dino = next((g.value for g in results if g.name == "dino_band"), None)
    return verdict, [asdict(g) for g in results if not g.passed], dino


# FastAPI 스레드풀(기본 40)과 비슷한 크기 — 작으면 동시 요청이 몰릴 때 check_photo 가
# 큐에서 기다려 병렬화가 오히려 직렬보다 느려진다.
_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="pipeline")


def _in_background(fn, *args):
    """fn 을 다른 스레드에서 시작하고 Future 를 돌려준다. 현재 컨텍스트(Langfuse/OTEL
    트레이스)를 복사해 넘긴다 — 안 그러면 그 스레드의 VLM 호출이 이번 변환 트레이스에서
    떨어져 별도 트레이스로 찍힌다."""
    ctx = contextvars.copy_context()
    return _POOL.submit(ctx.run, fn, *args)


def validate_result(s: State) -> dict:
    """① 출력 가드 → ② 결과가 '제대로 된 사진'인지 VLM 으로 확인 (구도가
    잘렸거나 자막/텍스트가 상품을 가리면 invalid). 라우팅(_route_after_validate)이
    이 결과를 보고 generate 로 되돌릴지 결정한다.

    ②(check_photo)는 ①의 OCR 읽기와 서로 독립이라 동시에 시작한다 — 생성 1회당 VLM
    왕복 1회 절약. 대신 가드가 막은 시도에서도 ②가 호출되고 결과는 버려진다
    (아직 시작 전이면 취소)."""
    photo = _in_background(detector.check_photo, s["result"])   # 스스로 예외를 삼킨다
    try:
        verdict, report, dino = _run_guards(s)
    except BaseException:
        photo.cancel()
        raise
    if verdict == "block":
        photo.cancel()
        if s.get("guard_seed") is None:
            seed = random.randint(0, 2**32 - 1)
            print(f"[guards] hard fail, seed={seed} 로 1회 재시도: "
                  f"{[g['name'] for g in report]}")
            return {"guard_retry": True, "guard_seed": seed,
                    "guard_report": report}
        # 재시도도 fail → 생성본은 내보내지 않는다. 원본 물건 픽셀을 쓰는 배경 교체로
        # 보내고, 그마저 실패하면 composite 가 원본 그대로(blocked)로 끝낸다.
        print(f"[guards] 재시도도 hard fail → 배경 교체: {[g['name'] for g in report]}")
        return {"guard_retry": False, "guard_failed": True,
                "composite_reason": "guard_failed", "guard_report": report}

    storage.save("result", s["result_name"], s["result"])
    try:
        # 상한: 큐 대기 + VLM 타임아웃. 넘기면 구도 검사를 못 한 것 — check_photo 의
        # 기존 실패 정책(개방형, valid=True)과 같게.
        check = photo.result(timeout=settings.vlm_timeout_s + 10)
    except Exception as e:
        print(f"[validate_result] check_photo 대기 실패(무시): {e}")
        check = {"valid": True, "reason": ""}
    return {"photo_check": check, "guard_retry": False, "status": "pass",
            "guard_report": report, "visual_similarity": dino}


def _route_after_validate(s: State) -> str:
    if s.get("guard_retry"):
        return "retry"
    if s.get("guard_failed"):
        return "composite"
    check = s.get("photo_check") or {"valid": True}
    if check.get("valid", True):
        return "ok"
    if s.get("gen_attempts", 0) >= settings.max_generate_attempts:
        print(f"[validate_result] 재생성 한도 소진, 마지막 결과로 진행: "
              f"{check.get('reason')}")
        return "ok"
    print(f"[validate_result] invalid, 재생성 ({s.get('gen_attempts', 0)}/"
          f"{settings.max_generate_attempts}): {check.get('reason')}")
    return "retry"


def score_similarity(s: State) -> dict:
    """원본 vs 결과 DINOv2 코사인 유사도 — VLM judge와 별개인 로컬 벡터 점수."""
    if s.get("status") == "blocked":
        return {"visual_similarity": None}
    if s.get("visual_similarity") is not None:
        # validate_result 의 dino_band 가드가 같은 이미지로 이미 계산함 — 점수만 부착
        score("visual_similarity", s["visual_similarity"], data_type="NUMERIC")
        return {}
    try:
        sim = embedder.cosine_similarity(s["original"], s["result"])
    except Exception as e:
        print(f"[score_similarity] 실패(무시): {e}")
        return {"visual_similarity": None}
    score("visual_similarity", sim, data_type="NUMERIC")
    return {"visual_similarity": sim}


TEXT_VERIFY_MAX = 8   # 글자 사후검증 항목 상한 — 많을수록 VLM 오판 1건에 게이트가 흔들린다


def _covered_by(key: str, covered: str) -> bool:
    """detect print 앵커 문장에 이 글자가 이미 들어 있나 — 단어 단위로만, 3글자 이상만.
    ("on" 이 "print on chest" 에 걸려 검증에서 빠지는 식의 오탐 방지)"""
    if not covered or len(key) < 3:
        return False
    return re.search(rf"(?<!\w){re.escape(key)}(?!\w)", covered) is not None


def _key_texts(item_texts: list, *, covered: str = "") -> list[tuple[str, str]]:
    """사후검증(verify 체크리스트·OCR 가드)에 걸 글자 고르기 → [(글자, 대략 위치)].
    글자 하나하나가 통과 조건이 되므로 읽기 오차에 덜 흔들리는 것만 고른다:
    - 큰 글자(박스 면적순) 위주로 TEXT_VERIFY_MAX 개까지 — 성분표 같은 잔글씨는 제외
    - prompt_safe 가 잘라낸 긴 문장·1글자는 제외 (끊긴 문장·한 글자는 오판이 잦다)
    - covered(이미 다른 항목이 다루는 글자)에 들어 있으면 제외"""
    def area(t):
        if not all(k in t for k in ("x1", "y1", "x2", "y2")):
            return 0
        return (t["x2"] - t["x1"]) * (t["y2"] - t["y1"])

    out, seen = [], set()
    for t in sorted(item_texts or [], key=area, reverse=True):
        if len(out) >= TEXT_VERIFY_MAX:
            break
        raw = str(t.get("text", "")).strip()
        text = prompt_safe(raw)
        key, where = text.lower(), text_where(t)
        if (len(text) < 2 or len(raw) > TEXT_LOCK_MAX_CHARS
                or (key, where) in seen or _covered_by(key, covered)):
            continue
        seen.add((key, where))
        out.append((text, where))
    return out


def _verify_targets(s: State, *, include_texts: bool = True) -> list:
    """verify 체크리스트 = detect 앵커 + read_text 가 읽은 글자 (_key_texts 로 고름).

    TEXT_LOCK 은 생성 전에 "글자를 지켜라"고 부탁만 한다 — 실제로 지켜졌는지는
    여기서 게이트에 건다 (뭉개지면 preserved:false → 재생성/배경 교체).
    detect 가 같은 글자를 print 앵커로 이미 올렸으면 중복으로 넣지 않는다."""
    targets = list(s.get("anchors") or [])
    if not include_texts:
        return targets
    covered = " ".join(a.get("what", "") for a in targets
                       if a.get("category") == "print").lower()
    targets += [{"category": "print", "what": f'text: "{text}"',
                 "where": where or "on the item"}
                for text, where in _key_texts(s.get("item_texts"), covered=covered)]
    return targets


def verify(s: State) -> dict:
    checks, gate_passed = [], None
    if s.get("status") == "blocked":
        return {"checks": checks, "gate_passed": gate_passed}
    mode = s.get("mode", "generate")
    if s.get("detect_failed"):
        # 원본 하자 목록이 없으니 보존 여부를 확인할 수 없다. 합성본은 물건 픽셀이
        # 원본이라 None(검증 안 함), 생성본(합성 실패 후 생성 포함)은 통과로 치지 않는다.
        return {"checks": checks, "gate_passed": None if mode == "composite" else False}
    # 합성본은 물건 픽셀이 원본 — 글자 항목까지 걸면 VLM 오판만으로 "보존 안 됨"
    # 경고가 원본 픽셀 사진에 붙는다. detect 앵커만 확인한다.
    targets = _verify_targets(s, include_texts=(mode == "generate"))
    if not targets:
        return {"checks": checks, "gate_passed": gate_passed, "verify_failed": False}
    saved = storage.load("result", s["result_name"])
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        try:
            checks = detector.verify_and_locate(
                saved, targets, s.get("item", "object"), s.get("considered", []),
                strict=True)
            return {"checks": checks, "verify_failed": False,
                    "gate_passed": detector.all_preserved(checks, expected=len(targets))}
        except Exception as e:
            print(f"[verify] 실패 ({attempt}/{VERIFY_ATTEMPTS}): {e}")
            if not retryable(e):
                break
            if attempt < VERIFY_ATTEMPTS:
                time.sleep(DETECT_RETRY_DELAY_S)
    # 호출 실패(타임아웃·429·장애)는 "보존됨"이 아니다 — 예전엔 None 으로 남아 통과됐다.
    # 생성본은 통과로 치지 않고(→ 배경 교체), 합성본은 물건 픽셀이 원본이라 None.
    return {"checks": [], "verify_failed": True,
            "gate_passed": None if mode == "composite" else False}


def _route_after_verify(s: State) -> str:
    """게이트 실패 → (1) 사라진 하자를 알려주고 1회 재생성 → (2) 그래도 실패면
    원본 물건 픽셀을 그대로 쓰는 배경 교체 모드. 생성 모델이 작은 글씨·미세 하자를
    지키지 못하는 경우의 정직성 폴백."""
    if s.get("gate_passed") is not False or s.get("mode", "generate") != "generate":
        return "done"
    if s.get("detect_failed"):
        return "composite"   # 재생성해도 확인할 기준이 없다 (앞선 오리기 실패 후 생성한 경우)
    if s.get("verify_failed"):
        return "composite"   # 확인 자체를 못 했다 — 재생성해도 또 확인 못 할 가능성이 크다
    if not s.get("gate_retried"):
        return "regen"
    return "composite"


def mark_gate_retry(s: State) -> dict:
    lost = [prompt_safe(c["what"]) for c in s.get("checks", []) if not c.get("preserved")]
    note = ("\n\nIMPORTANT: a previous attempt lost or altered these defects/marks on the "
            "product: " + "; ".join(f'"{w}"' for w in lost if w) +
            ". They MUST remain exactly as in the input image.") if lost else ""
    print(f"[gate] 실패 → 1회 재생성: {lost}")
    return {"gate_retried": True, "gate_note": note,
            "photo_check": None, "guard_seed": None}


def composite(s: State) -> dict:
    """배경 교체 모드: 원본 물건을 오려 프리셋 배경 위에 합성 (물건 픽셀 보존).

    들어오는 길: plan(생성 전 판단) / validate_result(가드 불합격) / verify(게이트 실패)."""
    reason = s.get("composite_reason") or (
        "detect_failed" if s.get("detect_failed")
        else "verify_failed" if s.get("verify_failed") else "gate_failed")
    try:
        if s.get("composite_error"):
            # 이번 실행에서 이미 실패한 오리기 — 같은 입력으로 다시 부르지 않는다
            raise RuntimeError(f"이전 오리기 실패: {s['composite_error']}")
        out = compositor.compose(s["original"], s["preset"]["bg_color"], s.get("item_box"))
    except Exception as e:
        if s.get("guard_failed"):
            # 생성본은 가드 불합격, 합성도 불가 → 원본 그대로 내보낸다 (침묵 대신 고지)
            print(f"[composite] 실패 + 가드 불합격 → blocked(원본): {e}")
            storage.save("result", s["result_name"], s["original"])
            return {"mode": "composite_failed", "composite_error": str(e),
                    "status": "blocked", "result": s["original"], "photo_check": None,
                    # verify 를 거치지 않고 끝나는 경로 — 뒷단이 읽는 값을 채워 둔다
                    "checks": [], "gate_passed": None}
        if s.get("result") is None:
            # 생성 전에 왔다(plan) — 이제 정상 생성 경로로
            print(f"[composite] 실패, 생성으로 진행: {e}")
            return {"mode": "generate", "composite_error": str(e), "composite_reason": None}
        # 오리기 실패 — 마지막 생성 결과를 그대로 두고 (게이트 실패 기록은 남음) 끝낸다
        print(f"[composite] 실패, 생성 결과 유지: {e}")
        return {"mode": "composite_failed", "composite_error": str(e)}
    print(f"[composite] 배경 교체 모드로 전환 ({reason})")
    storage.save("result", s["result_name"], out)
    # 합성본은 생성본이 아니다 — 생성본 기준 검사 결과(구도 검사·가드·유사도)를 들고 가지 않는다
    return {"result": out, "mode": "composite", "composite_error": None,
            "composite_reason": reason, "photo_check": None,
            # 가드 불합격으로 왔으면 어떤 가드가 걸렸는지는 기록으로 남긴다
            "guard_report": s.get("guard_report", []) if s.get("guard_failed") else [],
            "status": "pass", "visual_similarity": None,
            "prompt_used": "COMPOSITE: original item pixels on preset background"}


def _route_after_composite(s: State) -> str:
    if s.get("mode") == "composite":
        return "ok"
    if s.get("mode") == "generate":
        return "generate"   # 생성 전 합성이 실패 — 정상 생성 경로로
    return "failed"


def save_inspect(s: State) -> dict:
    """디버그 인스펙트 (classify 흔적 포함 = 투명성)."""
    storage.save(
        "quality",
        f"{s['file_id']}_{s['preset_key']}_inspect.json",
        json.dumps({
            "item": s.get("item", "object"),          # ⭐
            "considered": s.get("considered", []),    # ⭐
            "anchors": s["anchors"],
            "detect_failed": s.get("detect_failed", False),
            "verify_failed": s.get("verify_failed", False),
            "item_texts": s.get("item_texts", []),
            "mode": s.get("mode", "generate"),
            "composite_reason": s.get("composite_reason"),
            "guard_failed": s.get("guard_failed", False),
            "gate_retried": s.get("gate_retried", False),
            "composite_error": s.get("composite_error"),
            "checks": s.get("checks", []),
            "gate_passed": s.get("gate_passed"),
            "visual_similarity": s.get("visual_similarity"),   # ⭐
            "gen_attempts": s.get("gen_attempts"),             # ⭐
            "photo_check": s.get("photo_check"),               # ⭐
            "status": s.get("status", "pass"),
            "guard_seed": s.get("guard_seed"),
            "guard_report": s.get("guard_report", []),
        }, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return {}


def _clear_quality(name: str) -> None:
    """옛 성적표 삭제 — 실패해도(예: S3 DeleteObject 권한 없음) 이미 비용을 쓴 변환을
    500 으로 날리지 않는다."""
    try:
        storage.delete("quality", name)
    except Exception as e:
        print(f"[judge] 옛 성적표 삭제 실패(무시): {e}")


def judge_and_save(file_id: str, preset_key: str, *, trace_id: str | None = None,
                   parent_span_id: str | None = None) -> None:
    """품질 성적표 채점 → 저장 → 축별 점수 부착. 채점 구현은 여기 하나뿐이다.

    그래프 밖에서 부른다 — 채점 결과를 기다려야 하는 단계가 그래프에 없고
    (judge 는 결정에 안 쓰이는 관측 신호), 호출부마다 "언제" 채점할지가 달라서:
      - transform 라우트: 응답을 보낸 뒤 BackgroundTasks 로 (사용자가 VLM 왕복을 안 기다림).
        요청 컨텍스트가 끝났으니 trace_id/parent_span_id 로 같은 트레이스에 이어 붙인다.
      - run_transform 기본값(ingest 등) · run_transform_with_result(dev): 그래프 직후
        바로 (현재 트레이스에 자동 중첩).
    원본·결과는 storage 에서 읽는다 — 사용자에게 서빙되는 저장본(정규화 후)을 채점
    (예전 그래프 노드는 fal 이 준 정규화 전 바이트를 채점 — 과거 점수와 소폭 차이 가능).
    채점 중에 같은 쌍이 다시 변환되면(결과 이미지가 바뀜) 옛 결과 점수는 버린다.
    blocked(원본을 내보냄)면 호출부가 부르지 않는다. 실패는 모두 삼킨다 (관측 신호)."""
    name = f"{file_id}_{preset_key}"
    try:
        original = storage.load_original(file_id)
        result = storage.load("result", f"{name}.jpg")
        if original is None or result is None:
            return
        with observe("judge_and_save", as_type="span", trace_id=trace_id,
                     parent_span_id=parent_span_id,
                     input={"file_id": file_id, "preset_key": preset_key}):
            report = judge.judge(original, result)
            if not report:
                return
            if storage.load("result", f"{name}.jpg") != result:
                print("[judge] 채점 중 결과가 바뀜 → 버림")
                return
            storage.save("quality", f"{name}.json",
                         json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"))
            if storage.load("result", f"{name}.jpg") != result:
                # 확인~저장 사이에 재변환이 끼어듦 — 새 결과에 옛 점수가 남지 않게 지운다
                _clear_quality(f"{name}.json")
                return
            try:
                for axis in AXES:
                    score(axis, report[axis], data_type="NUMERIC")
            except Exception as e:
                print(f"[judge] 점수 부착 실패(무시): {e}")
    except Exception as e:
        print(f"[judge] 실패(무시): {e}")
    finally:
        flush()


def _record_result_safe(file_id, preset_key, result_name, item, considered,
                         gate_passed, bubbles, elapsed_s):
    """DB 기록은 detect/verify/judge와 같은 원칙 — 실패해도 이미 끝난(비용 든)
    변환 자체를 실패로 되돌리지 않는다."""
    try:
        store.record_result(file_id, preset_key, result_name, item, considered,
                             gate_passed, bubbles, elapsed_s=elapsed_s)
    except Exception as e:
        print(f"[record_result] 실패(무시): {e}")


def finalize(s: State) -> dict:
    checks = s.get("checks", [])
    bubbles = detector.bubbles(checks)
    logger.info(f"[classify] item={s.get('item')} "
                f"considered={len(s.get('considered', []))}")
    logger.info(f"[detect] anchors={len(s['anchors'])}")
    logger.info(f"[verify] preserved="
                f"{sum(c['preserved'] for c in checks)}/{len(checks)}")
    logger.info(f"[validate_result] attempts={s.get('gen_attempts')} "
                f"photo_check={s.get('photo_check')}")
    logger.info(f"transform {s['file_id']}/{s['preset_key']} "
                f"bubbles={bubbles} gate={s.get('gate_passed')}")
    return {"bubbles": bubbles}


def use_provided(s: State) -> dict:
    """dev 그래프 전용: generate 대신 주어진 결과 이미지를 쓴다."""
    storage.save("result", s["result_name"], s["provided_result"])   # 실제 경로와 동일하게 정규화됨
    return {"result": storage.load("result", s["result_name"]),
            "prompt_used": "TEST: provided result (generate skipped)"}


# ── 조립 ─────────────────────────────────────────
def build(*, dev: bool = False):
    """dev=True: generate 를 use_provided 로 바꾸고 재생성·배경 교체 루프를 뺀 그래프.
    앞단(read_text ∥ detect)과 뒷단 노드는 운영 그래프와 같은 함수·같은 연결을 쓴다
    — verify 프롬프트 튜닝 결과가 운영과 어긋나지 않게 (judge 는 그래프 밖, 같은 함수)."""
    g = StateGraph(State)
    nodes = [("load", load), ("classify", classify_node),
             ("read_text", read_text), ("detect", detect), ("plan", plan),
             ("score_similarity", score_similarity), ("verify", verify),
             ("save_inspect", save_inspect),
             ("finalize", finalize)]
    nodes += ([("use_provided", use_provided)] if dev else
              [("generate", generate), ("validate_result", validate_result),
               ("mark_gate_retry", mark_gate_retry), ("composite", composite)])
    for n, f in nodes:
        g.add_node(n, f)

    g.add_edge(START, "load")
    g.add_edge("load", "classify")
    # read_text 와 detect 는 둘 다 classify 결과만 쓰고 서로 독립 — 병렬로 돌리고
    # 둘 다 끝나면 plan (VLM 왕복 1회분 지연 절약). 쓰는 state 키도 겹치지 않는다.
    g.add_edge("classify", "read_text")
    g.add_edge("classify", "detect")
    g.add_edge(["read_text", "detect"], "plan")

    if dev:
        g.add_edge("plan", "use_provided")
        g.add_edge("use_provided", "score_similarity")
        g.add_edge("score_similarity", "verify")
        g.add_edge("verify", "save_inspect")
    else:
        g.add_conditional_edges("plan", _route_after_plan,
                                {"generate": "generate", "composite": "composite"})
        g.add_edge("generate", "validate_result")
        g.add_conditional_edges("validate_result", _route_after_validate,
                                {"retry": "generate", "ok": "score_similarity",
                                 "composite": "composite"})
        g.add_edge("score_similarity", "verify")
        g.add_conditional_edges("verify", _route_after_verify,
                                {"done": "save_inspect", "regen": "mark_gate_retry",
                                 "composite": "composite"})
        g.add_edge("mark_gate_retry", "generate")
        g.add_conditional_edges("composite", _route_after_composite,
                                {"ok": "score_similarity", "failed": "save_inspect",
                                 "generate": "generate"})
    g.add_edge("save_inspect", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


GRAPH = build()

# 최악 경로 = 앞 4단계(load·classify·read_text∥detect·plan) + composite(생성 전, 실패)
# + (generate·validate) × (재생성 한도 + 가드 재시도) × 2(게이트 재생성)
# + score/verify 3회 + composite + 뒷 2노드 ≈ 35. max_generate_attempts=5 여도 넉넉하게.
# (LangGraph 기본 25 는 기본 설정에서도 경계라, 비용을 다 쓴 뒤 예외로 끝날 수 있었다)
RECURSION_LIMIT = 80


def run_transform(file_id: str, preset_key: str, *, defer_judge: bool = False) -> dict:
    """defer_judge=True: 채점하지 않고 결과에 judge_pending/trace_id 를 실어 보낸다 —
    호출부(transform 라우트)가 응답 뒤 judge_and_save() 를 돌린다.
    기본값(False)은 그래프 직후 여기서 바로 채점 (ingest 등 배치 호출부)."""
    t0 = time.time()

    # pass 모드 = 지름길 (그래프 안 탐)
    if settings.pipeline_mode == "mock":
        original = storage.load_original(file_id)
        if original is None:
            raise FileNotFoundError(file_id)
        result_name = f"{file_id}_{preset_key}.jpg"
        storage.save("result", result_name, original)
        _clear_quality(f"{file_id}_{preset_key}.json")   # 옛 실행의 성적표
        _record_result_safe(file_id, preset_key, result_name, "object", [],
                             None, [], elapsed_s=time.time() - t0)
        return {"result_name": result_name, "prompt_used": "PASS-THROUGH",
                "checks": [], "bubbles": [], "gate_passed": None, "item": "object", "considered": [],
                "status": "pass", "guard_report": [], "mode": "generate"}

    try:
        with observe("transform", as_type="span",
                     input={"file_id": file_id, "preset_key": preset_key},
                     metadata={"pipeline_mode": settings.pipeline_mode}) as obs:
            # 옛 성적표는 시작할 때 지운다 — 파일명이 file_id/preset 뿐이라 남겨 두면
            # 새 결과에 옛 점수가 붙는다 (blocked·채점 실패 때도)
            _clear_quality(f"{file_id}_{preset_key}.json")
            out = GRAPH.invoke({"file_id": file_id, "preset_key": preset_key},
                               {"recursion_limit": RECURSION_LIMIT})
            # 그래프 도중(새 결과 저장 전)에 이전 요청의 백그라운드 채점이 옛 결과 점수를
            # 저장했을 수 있다 — 새 결과가 저장된 뒤 한 번 더 지운다. 이후에 그 채점이
            # 저장하려 하면 judge_and_save 의 재확인(결과 바뀜)이 스스로 지운다.
            _clear_quality(f"{file_id}_{preset_key}.json")
            judged = out.get("status", "pass") != "blocked"   # 원본을 내보내면 채점 안 함
            if judged and not defer_judge:
                judge_and_save(file_id, preset_key)

            result = {
                "result_name": out["result_name"],
                "prompt_used": out["prompt_used"],
                "checks": out["checks"],
                "bubbles": out["bubbles"],
                "gate_passed": out["gate_passed"],
                "item": out.get("item", "object"),
                "considered": out.get("considered", []),
                "visual_similarity": out.get("visual_similarity"),
                "gen_attempts": out.get("gen_attempts"),
                "photo_check": out.get("photo_check"),
                "status": out.get("status", "pass"),
                "guard_report": out.get("guard_report", []),
                "mode": out.get("mode", "generate"),
                "composite_reason": out.get("composite_reason"),
                "detect_failed": out.get("detect_failed", False),
                "verify_failed": out.get("verify_failed", False),
                "judge_pending": defer_judge and judged,
                "trace_id": current_trace_id(),
                "trace_span_id": getattr(obs, "id", None),
            }

            if obs is not None:
                obs.update(output={
                    "gate_passed": result["gate_passed"],
                    "bubbles": len(result["bubbles"]),
                    "item": result["item"],
                    "visual_similarity": result["visual_similarity"],
                    "gen_attempts": result["gen_attempts"],
                    "photo_check": result["photo_check"],
                    "mode": result["mode"],
                    "composite_reason": result["composite_reason"],
                    "detect_failed": result["detect_failed"],
                    "verify_failed": result["verify_failed"],
                })
    finally:
        # 성공/실패(예외) 상관없이 이번 요청의 트레이스는 즉시 내보낸다 —
        # 실패 트레이스가 배치 주기까지 안 보내지고 프로세스 종료로 유실되는 걸 방지.
        flush()
    elapsed_s = time.time() - t0
    logger.info(f"total {elapsed_s:.1f}s")
    _record_result_safe(file_id, preset_key, result["result_name"], result["item"],
                         result["considered"], result["gate_passed"], result["bubbles"],
                         elapsed_s)
    return result


def run_transform_with_result(file_id: str, preset_key: str, result_bytes: bytes) -> dict:
    """dev 전용: generate() 를 건너뛰고 주어진 결과 이미지로 이후 단계
    (verify/judge/finalize)만 돌린다 — fal.ai 를 매번 기다리지 않고
    verify/judge 프롬프트를 반복 튜닝하기 위함.

    운영과 같은 노드 함수·같은 앞단 연결(read_text ∥ detect)을 쓰는 dev 그래프
    (build(dev=True))로 돌린다 — 손으로 노드를 이어 부르면 운영 그래프가 바뀔 때
    조용히 어긋난다 (로직 복사 금지: 테스트는 프로덕션을 호출하지, 베끼지 않는다).
    """
    t0 = time.time()
    try:
        with observe("transform_dev", as_type="span",
                     input={"file_id": file_id, "preset_key": preset_key},
                     metadata={"generate_skipped": True}) as obs:
            _clear_quality(f"{file_id}_{preset_key}.json")
            # 매 호출 빌드 — 모듈의 노드 함수를 호출 시점에 묶는다 (dev 경로라 비용 무시 가능)
            s = build(dev=True).invoke(
                {"file_id": file_id, "preset_key": preset_key,
                 "provided_result": result_bytes},
                {"recursion_limit": RECURSION_LIMIT})
            _clear_quality(f"{file_id}_{preset_key}.json")   # run_transform 과 같은 이유
            judge_and_save(file_id, preset_key)   # judge 프롬프트 튜닝용 — 바로 채점

            result = {
                "result_name": s["result_name"],
                "prompt_used": s["prompt_used"],
                "checks": s["checks"],
                "bubbles": s["bubbles"],
                "gate_passed": s["gate_passed"],
                "item": s.get("item", "object"),
                "considered": s.get("considered", []),
                "visual_similarity": s.get("visual_similarity"),
                "detect_failed": s.get("detect_failed", False),
                "verify_failed": s.get("verify_failed", False),
                "mode": s.get("mode", "generate"),
                "status": s.get("status", "pass"),
            }
            if obs is not None:
                obs.update(output={
                    "gate_passed": result["gate_passed"],
                    "bubbles": len(result["bubbles"]),
                    "item": result["item"],
                    "visual_similarity": result["visual_similarity"],
                })
    finally:
        flush()
    elapsed_s = time.time() - t0
    logger.info(f"[test] total {elapsed_s:.1f}s (generate skipped)")
    _record_result_safe(file_id, preset_key, result["result_name"], result["item"],
                         result["considered"], result["gate_passed"], result["bubbles"],
                         elapsed_s)
    return result