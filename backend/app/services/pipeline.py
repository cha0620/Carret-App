"""변환 파이프라인 - LangGraph 판.

스테이지:
  0) load       : 원본 로드
  1) analyze    : 물건·아이덴티티 마크(앵커)·사진 종류·하자 수준·워터마크·글자 수준·물건 위치
                  (VLM 1회, 예전 classify + detect. 실패는 detect_failed)
  1.5) plan     : 사진 종류(photo_type) 셋으로 먼저 나눈다 —
         document(보증서·영수증·설명서처럼 진짜 문서) → 펴서 배경 교체 (생성 안 함).
           책·CD·음반은 10-03 부터 product (글자 양으로)
         inside_view(엔진룸·뜯은 노트북 회로처럼 물건 일부·내부) → 원본 그대로
         product → 생성해도 못 지킬 게 보이면(분석 실패·글자 dense·하자 heavy) 배경 교체,
                   글자가 있으면(simple) read_text(원본 글자 읽기) 뒤 생성, 없으면(none) 바로 생성
  2) generate   : 배경 교체 (생성)
  2.3) validate_result : ① 구도/자막 검사 — invalid면 max_generate_attempts 까지 재생성.
       ② 관측용(soft) 신호: 이미지 전체 DINO(dino_band), (켜면) 로컬 OCR — ①을 기다리는 동안 돈다.
       결과를 막는(hard) 가드는 없다 — VLM 글자 비교 가드는 읽기 흔들림 오반려가 많아 09-27 에
       없앴고, 글자는 TEXT_LOCK + verify 가 맡는다
  2.5) score_similarity : 원본 vs 결과 DINOv2 코사인 유사도 (가드 값 재사용)
  3) verify     : 생성본 → 아이덴티티 마크·글자 보존 여부 + 좌표. 실패 → 1회 재생성 → composite
                  (배경 교체·원본 그대로는 물건 픽셀이 원본이라 부르지 않는다)
                  (하자는 목록으로 확인하지 않는다 — 수준(wear_level)으로 plan 에서만 본다)
  4) finalize   : bubbles + 로깅
  (그래프 밖) judge_and_save : 품질 성적표 — 그래프가 끝난 뒤 한 곳에서만 채점.
       item_signals_and_save : 누끼 비교(item_dino·item_patch, 관측용) — 예전엔 validate_result 가
       기다렸는데 누끼(rembg CPU)가 3~43초로 생성 경로 시간의 가장 큰 덩어리였다 (09-29 Langfuse).
       결과를 바꾸지 않는 값이라 그래프 밖으로 뺐다.
       둘 다 transform 라우트는 응답 뒤 백그라운드, 그 외(run_transform 기본값 ·
       run_transform_with_result)는 그래프 직후 바로
"""

import contextvars
import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.core.tracing import current_trace_id, flush, observe, score
from app.core.vlm import retryable
from app.prompts.presets import (TEXT_LOCK_MAX_CHARS, get_preset, prompt_safe, unreadable,
                                 text_lock, text_where)
from app.prompts.rubric import AXES
from app.services import compositions
from app.services.ai import compositor, detector, embedder, judge
from app.services.ai.generator import _generate_ai
from app.services.persistence import storage, store
from app.services.quality import guards

logger = logging.getLogger("carret.pipeline")


class State(TypedDict, total=False):
    file_id: str
    preset_key: str            # 스타일 키 (무드, 한 줄이 있으면 무드-해시) — 저장 이름·DB 키
    style_note: str            # 사용자가 적은 무드 한 줄 (clean_note 통과) — 배경에만
    composition: str | None    # 고른 정석 구도 키 (services/compositions.py) — 틀만, 각도는 그대로
    preset: dict
    original: bytes
    item: str                 # ⭐ analyze 산출
    considered: list          # ⭐ analyze 산출 (verify 체크리스트 힌트)
    anchors: list              # 아이덴티티 마크 (category="print") — verify 대상
    photo_type: str | None     # analyze: document | inside_view | product (실패면 None)
    wear_level: str | None     # analyze: none | light | heavy (실패면 None)
    watermark: str | None      # analyze: none | background | on_item (관측만 — 합성 모드는 물건 위 워터마크를 못 지운다)
    detect_failed: bool        # analyze 가 재시도까지 실패 — "지킬 것 없음"과 구분 (검증 불가). 이름은 예전 detect 호환
    verify_failed: bool        # verify 호출이 재시도까지 실패 — "보존됨"과 구분 (검증 불가)
    item_texts: list           # 원본 물건 위 글자 [{text, x1..}] → generate 프롬프트
    text_level: str | None     # analyze 가 본 물건 위 글자 수준: none | simple | dense(잔글씨·원본에서도 못 읽음) (실패면 None)
    item_box: dict | None      # 원본에서 물건 위치 (0-1000) — 오리기 범위 (배경 교체 · item_dino)
    item_count: int            # analyze: 팔려는 물건 개수 — 2개 이상이면 표지를 펴지 않는다
    gate_retried: bool         # verify 게이트 실패로 재생성을 이미 1회 했나
    gate_note: str             # 그 재생성 때 프롬프트에 붙인 "사라진 하자" 목록
    mode: str                  # "generate" | "composite"(원본 물건 + 배경만 교체) | "composite_failed"
                               #   | "original"(원본 그대로 — inside_view 등)
    composite_reason: str | None   # 생성하지 않은 이유: detect_failed | inside_view | document | text_dense
                                   #   | text_heavy | wear_heavy | gate_failed | verify_failed
    provided_result: bytes     # dev 그래프: generate 대신 쓸 결과 이미지
    composite_error: str | None
    result: bytes
    result_name: str
    gen_attempts: int          # ⭐ generate 시도 횟수 (재생성 게이트용)
    photo_check: dict          # ⭐ {"valid": bool, "reason": str} - 구도/자막 검사
    guard_report: list         # 기준 밖인 관측 신호 목록 (전부 soft) - asdict(GuardResult)
    visual_similarity: float | None   # ⭐ DINOv2 코사인 유사도 (이미지 전체)
    item_similarity: float | None     # 누끼 딴 물건끼리의 DINOv2 유사도 (item_dino 가드)
    item_patch_similarity: float | None   # 같은 누끼 쌍의 DINO 패치 하위 1% (item_patch 가드)
    ocr_local_recall: float | None    # 로컬 OCR 글자 recall (ocr_local 가드, 설정으로 켤 때만)
    checks: list
    gate_checks: list          # 배경 교체로 가기 전 생성본 게이트의 checks (무엇이 사라졌나 — 기록용)
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
        "preset": get_preset(s["preset_key"], note=s.get("style_note") or ""),
    }


def read_text(s: State) -> dict:
    """원본 물건 위 글자 읽기 — generate 프롬프트에 넣어 글자 뭉개짐을 줄인다.
    analyze 가 text_level=simple 이라고 본 물건만 온다 (none 은 읽을 게 없고, dense 는 이미
    배경 교체로 갔다). 관측 단계와 같은 실패 정책: 실패하면 글자 없이 그대로 생성한다.
    물건 위치(item_box)는 analyze 가 준 걸 우선 — 없을 때만 여기 값을 쓴다."""
    box = s.get("item_box")
    if not settings.text_lock:
        return {"item_texts": [], "item_box": box}
    if isinstance(s.get("item_texts"), list):
        # analyze(v2)가 이미 읽었다 — VLM 을 또 부르지 않는다 (10-01: 글자 읽기 호출이 VLM 비용의 21%,
        # 따로 부르면 응답이 깨져도 조용히 "글자 없음"이 됐다 — analyze 는 깨지면 재시도 · detect_failed)
        out = {"texts": s["item_texts"], "item_box": None}
    else:
        try:   # 옛 analyze 결과(texts 없음, 저장해 둔 것)만 여기로
            out = detector.read_item_text(s["original"], s.get("item", "object"))
        except Exception as e:
            print(f"[read_text] 실패(무시): {e}")
            return {"item_texts": [], "item_box": box}
    upd = {"item_texts": out["texts"], "item_box": box or out.get("item_box")}
    # analyze 가 simple 이라고 했어도 막상 읽어 보니 잔글씨가 많으면 — 생성 전 배경 교체 (안전망).
    # dev 그래프(provided_result)는 배경 교체로 가지 않는다 (inspect 에 엉뚱한 사유가 남지 않게).
    # 생성 전 배경 교체가 이미 실패하고 온 길(composite_error)이면 다시 보내지 않는다 (무한 왕복 방지).
    n = settings.composite_first_min_texts
    if (n and len(out["texts"]) >= n and s.get("provided_result") is None
            and not s.get("composite_error")):
        print(f"[read_text] 글자 {len(out['texts'])}줄 → 생성 전 배경 교체 모드로: text_heavy")
        upd["composite_reason"] = "text_heavy"
    return upd


ANALYZE_ATTEMPTS = 2
DETECT_RETRY_DELAY_S = 1.0   # 429/일시 장애가 바로 또 나지 않게 잠깐 쉰다 (verify 도 같이 씀)
VERIFY_ATTEMPTS = 2


def _analysis_name(file_id: str) -> str:
    return f"{file_id}_analysis.json"


def load_analysis(file_id: str) -> dict | None:
    """저장해 둔 analyze 결과 — 업로드 직후 미리 분석(/api/analyze)했거나 같은 사진을 다른 무드로
    다시 변환할 때 VLM 을 또 부르지 않게. 원본은 file_id 마다 바뀌지 않는다. 깨졌으면 None."""
    try:
        data = storage.load("quality", _analysis_name(file_id))
        return json.loads(data) if data else None
    except Exception:
        return None


def analyze_original(file_id: str, original: bytes) -> dict:
    """analyze VLM (재시도 포함) → 결과. 성공하면 저장해 둔다. 실패는 detect_failed=True (저장 안 함)."""
    for attempt in range(1, ANALYZE_ATTEMPTS + 1):
        try:
            out = {**detector.analyze(original), "detect_failed": False}
            try:
                storage.save("quality", _analysis_name(file_id),
                             json.dumps(out, ensure_ascii=False).encode("utf-8"))
            except Exception as e:
                print(f"[analyze] 결과 저장 실패(무시): {e}")
            return out
        except Exception as e:
            print(f"[analyze] 실패 ({attempt}/{ANALYZE_ATTEMPTS}): {e}")
            if not retryable(e):
                break
            if attempt < ANALYZE_ATTEMPTS:
                time.sleep(DETECT_RETRY_DELAY_S)
    return {"item": "object", "considered": [], "anchors": [], "detect_failed": True,
            "text_level": None, "photo_type": None, "wear_level": None, "watermark": None}


def analyze(s: State) -> dict:
    """원본 분석 (VLM 1회) — 물건·아이덴티티 마크·사진 유형·하자 수준·워터마크·글자 수준·위치.
    저장해 둔 결과가 있으면 그걸 쓴다 (load_analysis).

    실패는 "지킬 것 없음"과 다르다 — 빈 앵커로 넘기면 verify 가 검사할 게 없다며
    게이트를 통과시킨다. 재시도까지 실패하면 detect_failed 로 남겨 plan 이 배경 교체로 보낸다
    (생성 결과를 확인 없이 내보내지 않음)."""
    cached = load_analysis(s["file_id"])
    if cached is not None and not cached.get("detect_failed"):
        return cached
    return analyze_original(s["file_id"], s["original"])


def _result_name(s: State) -> str:
    return f"{s['file_id']}_{s['preset_key']}.jpg"


def _composite_first_reason(s: State) -> str | None:
    """생성 전에 이미 "생성하면 못 지킨다"가 보이는 경우 — FLUX·재생성·verify 비용을
    쓰지 않고 바로 원본 픽셀을 쓰는 배경 교체로 보낸다.
    inside_view 는 배경 교체도 하지 않는다 — plan 이 원본 그대로(keep_original)로 보낸다."""
    if s.get("detect_failed"):
        return "detect_failed"     # 생성해도 확인할 기준(원본 마크 목록)이 없다
    if s.get("photo_type") == "inside_view":
        # 엔진룸·뜯은 노트북처럼 물건 일부·내부만 찍힌 사진 — 생성은 없는 차체를 지어내고(09-27 굴삭기),
        # 오리기는 경계가 없어 엉뚱하게 잘린다. 배경을 바꿀 대상이 아니다
        return "inside_view"
    if s.get("photo_type") == "document":
        # 보증서·영수증·설명서 — 글자가 곧 물건이다 (10-03: 책·CD·음반은 상품이라 product 로 뺐다). 스펙·상태 글자(용량·주행거리)는 판매 정보로
        # 따로 받으면 되지만 제목·문서 내용은 대신할 곳이 없고, 한 글자만 바뀌어도 다른 물건이 된다 (09-27)
        return "document"
    if s.get("text_level") == "dense":
        return "text_dense"        # 잔글씨·라벨·눈금·원본에서도 못 읽는 글자 — 생성 모델이 뭉갠다 (글자 읽기도 생략)
    if s.get("wear_level") == "heavy":
        # 녹·도장 벗겨짐처럼 하자가 넓다 — 생성은 지우거나(굴삭기 범퍼) 과장한다(승용차 녹).
        # 원본 픽셀을 쓰는 배경 교체만 상태를 그대로 보여준다
        return "wear_heavy"
    # text_heavy(읽어 보니 잔글씨 多)는 read_text 가 정한다 — 여기선 아직 읽기 전이다
    return None


def plan(s: State) -> dict:
    """analyze 다음 갈림길. result_name 을 여기서 정해 두면 generate 를
    건너뛰는 경로(바로 composite)에서도 저장 이름이 있다."""
    out = {"result_name": _result_name(s)}
    if s.get("provided_result") is not None:
        return out   # dev 그래프 — 배경 교체로 가지 않는다 (inspect 에 엉뚱한 사유가 남지 않게)
    reason = _composite_first_reason(s)
    if reason:
        print(f"[plan] 생성하지 않음: {reason}")
        out["composite_reason"] = reason
    return out


def _needs_text(s: State) -> bool:
    """글자 읽기가 필요한가 — analyze 가 simple 이라고 봤을 때만. text_level 이 없으면(예전
    state·응답) simple 로 본다 = 예전처럼 읽는다. dense 는 배경 교체로 가서 여기 오지 않는다."""
    return settings.text_lock and (s.get("text_level") or "simple") == "simple"


def _route_after_plan(s: State) -> str:
    """원본 그대로(inside_view) | 배경 교체 | 글자 읽기 후 생성 | 바로 생성 (글자 없음).
    오리기마저 실패하면 composite 가 mode 를 generate 로 되돌려 그때 생성한다 — dense 로
    갔다가 돌아온 경우는 _route_after_composite 가 read_text 를 거치게 한다."""
    if s.get("composite_reason") == "inside_view":
        return "keep_original"
    if s.get("composite_reason"):
        return "composite"
    return "read_text" if _needs_text(s) else "generate"


def _route_after_read_text(s: State) -> str:
    return "composite" if s.get("composite_reason") else "generate"


def _route_after_plan_dev(s: State) -> str:
    return "read_text" if _needs_text(s) else "use_provided"


def generate(s: State) -> dict:
    preset = s["preset"]
    # 고른 정석 구도의 틀(가운데 · 여백 · 수평) — 각도는 잠금이 지킨다. 글자 잠금은 그 뒤에.
    # 물건이 여러 개면 구도를 붙이지 않는다 — 구도 문장은 한 개 기준이라 여럿을 하나로 합친다 (10-03 CD 2장)
    composition = s.get("composition") if (s.get("item_count") or 1) <= 1 else None
    extra = compositions.prompt_for(composition) + text_lock(s.get("item_texts") or [])
    if extra:
        preset = {**preset, "prompt": preset["prompt"] + extra}
    if s.get("gate_note"):
        preset = {**preset, "prompt": preset["prompt"] + s["gate_note"]}
    prior_check = s.get("photo_check")
    if prior_check and not prior_check.get("valid", True):
        # 재시도 — 직전 반려 사유를 프롬프트에 붙여서 "맹목적" 재시도가 되지
        # 않게 한다 (같은 프롬프트를 그대로 다시 던지면 같은 결함이 반복되기 쉬움).
        note = prior_check.get("reason") or "구도가 잘렸거나 텍스트가 상품을 가림"
        preset = {**preset, "prompt": preset["prompt"] + (
            f"\n\nIMPORTANT: a previous attempt was rejected for this reason: "
            f"\"{note}\". Show the FULL item in frame (no cropping/zoom) and do "
            f"NOT add any caption, subtitle, watermark, or overlaid text.")}
    gen = _generate_ai(s["original"], preset)
    result_name = _result_name(s)
    n = s.get("gen_attempts", 0) + 1
    if settings.keep_attempts:
        # 게이트에 걸려 배경 교체로 가면 생성본이 덮여 무엇이 바뀌었는지 볼 수 없다 (10-01) — eval 에서만 켠다
        try:
            storage.save("attempts", f"{s['file_id']}_{s['preset_key']}_try{n}.jpg", gen)
        except Exception as e:
            print(f"[generate] 시도 이미지 저장 실패(무시): {e}")
    # 저장은 validate_result 가 한다 — 검사 도중 예외로 끝난 이미지가 /storage/result/ 의
    # 예측 가능한 URL 에 남지 않게.
    return {
        "result": gen,
        "result_name": result_name,
        "prompt_used": preset["prompt"],
        "gen_attempts": n,
        "visual_similarity": None,   # 새 이미지 — 이전 결과 기준 값은 무효
        "item_similarity": None,
        "item_patch_similarity": None,
        "ocr_local_recall": None,
    }


def _item_pair(s: State) -> tuple[bytes, bytes] | None:
    """item_dino 가드 입력 — 원본·결과에서 물건만 오려 같은 배경·크기에 놓은 쌍.
    원본 오리기는 캐시돼 재생성마다 다시 하지 않는다. 오리기 실패는 None — soft 가드라
    이미 비용 든 생성을 이것 때문에 막지 않는다."""
    try:
        return (compositor.isolate(s["original"], s.get("item_box"), original=True),
                compositor.isolate(s["result"]))
    except Exception as e:
        print(f"[guards] 물건 오리기 실패(item_dino 생략): {e}")
        return None


def _dino_band(s: State) -> tuple[list, float | None]:
    """이미지 전체 DINO (dino_band, soft) → (기준 밖이면 기록, 유사도). 유사도는
    score_similarity 가 다시 계산하지 않게 넘긴다."""
    g = guards.dino_band_guard(s["original"], s["result"])
    if g is None:
        return [], None
    return ([] if g.passed else [asdict(g)]), g.value


def _item_signals(s: State) -> tuple:
    """누끼 쌍 가드 item_dino·item_patch — 오리기(rembg) + DINO 추론(패치는 448 해상도 2회)."""
    pair = _item_pair(s)
    return guards.item_guard(pair), guards.item_patch_guard(pair)


def _remaining(deadline: float) -> float:
    return max(0.0, deadline - time.monotonic())


def _local_ocr_lines(s: State) -> tuple[list[str], list[str]]:
    """ocr_local 가드 입력 — 원본은 물건 영역(item_box)만, 결과는 전체 (생성본의 물건 위치는
    원본과 다르고, 프리셋 배경엔 글자가 없다). EasyOCR 조각(단어 단위) 목록이라 read_text(VLM)의
    줄 목록과 단위가 다르다 — 관측용 값이다."""
    from app.services.ai import local_ocr
    return (local_ocr.read_original(s["original"], s.get("item_box")),
            local_ocr.read_lines(s["result"]))


def _local_ocr_check(fut, deadline: float) -> tuple[dict | None, float | None]:
    """ocr_local 결과 (실패했으면 가드 기록, recall). 꺼져 있거나 늦거나 실패하면 (None, None)."""
    if fut is None:
        return None, None
    try:
        g = guards.local_ocr_guard(*fut.result(timeout=_remaining(deadline)))
    except Exception as e:
        fut.cancel()
        print(f"[guards] 로컬 OCR 실패(ocr_local 생략): {e!r}")
        return None, None
    if g is None:
        return None, None
    return (None if g.passed else asdict(g)), g.value


# FastAPI 스레드풀(기본 40)과 비슷한 크기 — 작으면 동시 요청이 몰릴 때 check_photo 가
# 큐에서 기다려 병렬화가 오히려 직렬보다 느려진다.
_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix="pipeline")
# 로컬 OCR(EasyOCR)은 워커 1개 — 누끼 자리를 뺏지 않고, Reader 를 여러 스레드가 동시에 쓰지 않게
# (EasyOCR 은 스레드 안전을 보장하지 않는다).
_OCR_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ocr")
# 대기 상한은 작업을 넘긴 시점부터 — check_photo 를 기다리는 동안에도 흐른다 (직렬로 더해지지 않게).
LOCAL_OCR_WAIT_S = 60   # ocr_local — 첫 EasyOCR 로드(모델 다운로드) 포함 여유


def _in_background(fn, *args, pool=None):
    """fn 을 다른 스레드에서 시작하고 Future 를 돌려준다. 현재 컨텍스트(Langfuse/OTEL
    트레이스)를 복사해 넘긴다 — 안 그러면 그 스레드의 VLM 호출이 이번 변환 트레이스에서
    떨어져 별도 트레이스로 찍힌다."""
    ctx = contextvars.copy_context()
    return (pool or _POOL).submit(ctx.run, fn, *args)


def validate_result(s: State) -> dict:
    """결과가 '제대로 된 사진'인지 VLM 으로 확인 (구도가 잘렸거나 자막/텍스트가 상품을 가리면
    invalid) — 라우팅(_route_after_validate)이 보고 generate 로 되돌릴지 정한다.
    기다리는 동안 관측 신호(dino_band, 켜져 있으면 로컬 OCR)를 같이 잰다 — 전부 soft 라
    결과를 막지 않는다. 누끼 비교는 그래프 밖(item_signals_and_save)."""
    photo = _in_background(detector.check_photo, s["result"])   # 스스로 예외를 삼킨다
    t0 = time.monotonic()
    # 로컬 OCR — 원본에 글자가 있을 때만 (없으면 비교할 게 없다)
    ocr = (_in_background(_local_ocr_lines, s, pool=_OCR_POOL)
           if settings.local_ocr_guard and s.get("item_texts") else None)
    try:
        report, dino = _dino_band(s)
        storage.save("result", s["result_name"], s["result"])
    except BaseException:
        for f in (photo, ocr):
            if f is not None:
                f.cancel()
        raise
    try:
        # 상한: 큐 대기 + VLM 타임아웃. 넘기면 구도 검사를 못 한 것 — check_photo 의
        # 기존 실패 정책(개방형, valid=True)과 같게.
        check = photo.result(timeout=settings.vlm_timeout_s + 10)
    except Exception as e:
        print(f"[validate_result] check_photo 대기 실패(무시): {e}")
        check = {"valid": True, "reason": ""}
    ocr_fail, ocr_recall = _local_ocr_check(ocr, t0 + LOCAL_OCR_WAIT_S)
    report = report + ([ocr_fail] if ocr_fail else [])
    return {"photo_check": check, "guard_report": report, "visual_similarity": dino,
            "ocr_local_recall": ocr_recall}


def _route_after_validate(s: State) -> str:
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
    """사후검증(verify 체크리스트)에 걸 글자 고르기 → [(글자, 대략 위치)].
    글자 하나하나가 통과 조건이 되므로 읽기 오차에 덜 흔들리는 것만 고른다:
    - 큰 글자(박스 면적순) 위주로 TEXT_VERIFY_MAX 개까지 — 성분표 같은 잔글씨는 제외
    - prompt_safe 가 잘라낸 긴 문장·1글자는 제외 (끊긴 문장·한 글자는 오판이 잦다)
    - covered(이미 다른 항목이 다루는 글자)에 들어 있으면 제외
    - 원본에서도 못 읽은 글자("?" 포함 — 읽기 프롬프트가 못 읽는 글자를 ? 로 쓰게 한다)는 제외:
      생성본이 "?" 와 일치할 수 없어 멀쩡한 결과도 반드시 떨어진다 (번호판 "17? 5433", 09-27)"""
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
        if (len(text) < 2 or len(raw) > TEXT_LOCK_MAX_CHARS or unreadable(raw)
                or (key, where) in seen or _covered_by(key, covered)):
            continue
        seen.add((key, where))
        out.append((text, where))
    return out


def _verify_targets(s: State) -> list:
    """verify 체크리스트 = detect 앵커 + read_text 가 읽은 글자 (_key_texts 로 고름).

    TEXT_LOCK 은 생성 전에 "글자를 지켜라"고 부탁만 한다 — 실제로 지켜졌는지는
    여기서 게이트에 건다 (뭉개지면 preserved:false → 재생성/배경 교체).
    detect 가 같은 글자를 print 앵커로 이미 올렸으면 중복으로 넣지 않는다."""
    targets = list(s.get("anchors") or [])
    covered = " ".join(a.get("what", "") for a in targets
                       if a.get("category") == "print").lower()
    targets += [{"category": "print", "what": f'text: "{text}"',
                 "where": where or "on the item"}
                for text, where in _key_texts(s.get("item_texts"), covered=covered)]
    return targets


def verify(s: State) -> dict:
    checks, gate_passed = [], None
    if s.get("mode", "generate") != "generate":
        # 배경 교체본은 물건 픽셀이 원본이다 — 확인해도 VLM 오판으로 거짓 경고만 붙을 수 있고
        # 결과를 바꾸지도 않는다 (VLM 1회 절약, 09-27). verify_failed 는 건드리지 않는다 —
        # "verify 호출 실패 → 배경 교체"면 그 사실이 배지·inspect 에 남아야 한다.
        # 생성본 게이트에서 떨어진 checks(무엇이 사라졌나)는 gate_checks 로 옮겨 남긴다
        # (checks 는 말풍선 좌표라 생성본 기준 — 합성본 위에 그리면 안 된다)
        out = {"checks": checks, "gate_passed": None}
        if s.get("checks"):
            out["gate_checks"] = s["checks"]
        return out
    if s.get("detect_failed"):
        # 원본 마크 목록이 없으니 보존 여부를 확인할 수 없다 — 생성본은 통과로 치지 않는다
        return {"checks": checks, "gate_passed": False, "verify_failed": False}
    targets = _verify_targets(s)
    if not targets:
        return {"checks": checks, "gate_passed": gate_passed, "verify_failed": False}
    saved = storage.load("result", s["result_name"])
    for attempt in range(1, VERIFY_ATTEMPTS + 1):
        try:
            checks = detector.verify_and_locate(
                saved, targets, s.get("item", "object"), strict=True, marks=True)
            return {"checks": checks, "verify_failed": False,
                    "gate_passed": detector.all_preserved(checks, expected=len(targets))}
        except Exception as e:
            print(f"[verify] 실패 ({attempt}/{VERIFY_ATTEMPTS}): {e}")
            if not retryable(e):
                break
            if attempt < VERIFY_ATTEMPTS:
                time.sleep(DETECT_RETRY_DELAY_S)
    # 호출 실패(타임아웃·429·장애)는 "보존됨"이 아니다 — 통과로 치지 않는다 (→ 배경 교체)
    return {"checks": [], "verify_failed": True, "gate_passed": False}


def _route_after_verify(s: State) -> str:
    """게이트 실패 → (1) 사라지거나 바뀐 마크를 알려주고 1회 재생성 → (2) 그래도 실패면
    원본 물건 픽셀을 그대로 쓰는 배경 교체 모드. 생성 모델이 로고·글자를
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
    note = ("\n\nIMPORTANT: a previous attempt lost or altered these marks on the "
            "product: " + "; ".join(f'"{w}"' for w in lost if w) +
            ". They MUST remain exactly as in the input image.") if lost else ""
    print(f"[gate] 실패 → 1회 재생성: {lost}")
    return {"gate_retried": True, "gate_note": note,
            "photo_check": None}


def _original_as_result(s: State) -> dict:
    """원본을 결과로 내보낼 때 뒷단(save_inspect·finalize·run_transform)이 읽는 값을 한 곳에서 채운다."""
    storage.save("result", s["result_name"], s["original"])
    return {"result": s["original"], "mode": "original",
            "checks": [], "gate_passed": None, "photo_check": None, "verify_failed": False,
            "guard_report": [], "prompt_used": "ORIGINAL: background not replaced"}


def keep_original(s: State) -> dict:
    """원본 그대로 (plan: inside_view) — 물건 일부·내부 사진은 생성도 오리기도 하지 않는다.
    물건 픽셀도 배경도 원본이라 검사할 것이 없다 (verify·judge 생략)."""
    print("[keep_original] 물건 일부·내부 사진 → 원본 그대로")
    return _original_as_result(s)


def composite(s: State) -> dict:
    """배경 교체 모드: 원본 물건을 오려 프리셋 배경 위에 합성 (물건 픽셀 보존).

    들어오는 길: plan(생성 전 판단) / read_text(12줄 이상) / verify(게이트 실패·호출 실패)."""
    reason = s.get("composite_reason") or (
        "detect_failed" if s.get("detect_failed")
        else "verify_failed" if s.get("verify_failed") else "gate_failed")
    try:
        if s.get("composite_error"):
            # 이번 실행에서 이미 실패한 오리기 — 같은 입력으로 다시 부르지 않는다
            raise RuntimeError(f"이전 오리기 실패: {s['composite_error']}")
        # 문서(보증서·영수증)는 정면으로 펴서 놓는다 (못 펴면 compose_flat 이 일반 배경 교체로).
        # 하자가 넓은 문서(찢김·접힘)는 펴지 않는다 — 네 모서리에 맞추면 찢어진 모서리가 잘리고
        # 접힌 자국이 펴져 상태가 좋아 보인다
        # 여러 개(종이 여러 장)도 펴지 않는다 — 붙어 있으면 한 사각형으로 합쳐 펴거나,
        # 한 개만 펴고 나머지를 지운다 (09-29 CD 2장: 한 장이 사라짐)
        flat = (reason == "document" and s.get("wear_level") != "heavy"
                and (s.get("item_count") or 1) <= 1)
        if flat:
            out = compositor.compose_flat(s["original"], s["preset"]["bg_color"], s.get("item_box"))
        else:
            # 문서는 펴지 않아도(여러 개·하자 heavy) 납작한 인쇄물 — 윤곽에 붙은 가는 조각을 뗀다
            out = compositor.compose(s["original"], s["preset"]["bg_color"], s.get("item_box"),
                                     flat=reason == "document")
    except Exception as e:
        if s.get("result") is None and reason in ("wear_heavy", "document"):
            # 하자가 넓어 "생성하면 지우거나 지어낸다"고 본 사진 / 책·음반처럼 한 글자만 바뀌어도
            # 다른 물건 — 오리기가 안 되면 생성하지 않고 원본을 그대로 보여준다
            # (생성으로 가면 하자·잔글씨 검사 없이 "보존됨" 배지가 붙는다)
            print(f"[composite] 실패 + {reason} → 원본 그대로: {e}")
            return {**_original_as_result(s), "composite_error": str(e), "composite_reason": reason}
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
            "guard_report": [], "visual_similarity": None, "item_similarity": None,
            "item_patch_similarity": None, "ocr_local_recall": None,
            "prompt_used": "COMPOSITE: original item pixels on preset background"}


def _route_after_composite(s: State) -> str:
    if s.get("mode") == "composite":
        return "ok"
    if s.get("mode") == "original":
        return "failed"   # 하자 heavy·document 인데 오리기 실패 → 원본 그대로, 검사할 것 없음 (save_inspect 로)
    if s.get("mode") == "generate":
        # 생성 전 합성이 실패 — 정상 생성 경로로. dense·detect_failed 로 왔다면 글자를 아직 안
        # 읽었다: 글자 없이 생성하면 TEXT_LOCK·verify 글자 확인이 통째로 빠져, 글자가 제일 많은 물건이
        # 보호를 제일 덜 받는다. 글자가 없다고 확인된(none) 경우만 바로 생성.
        if (settings.text_lock and s.get("text_level") != "none"
                and "item_texts" not in s):
            return "read_text"
        return "generate"
    return "failed"


def save_inspect(s: State) -> dict:
    """디버그 인스펙트 (analyze 흔적 포함 = 투명성)."""
    storage.save(
        "quality",
        f"{s['file_id']}_{s['preset_key']}_inspect.json",
        json.dumps({
            "item": s.get("item", "object"),          # ⭐
            "considered": s.get("considered", []),    # ⭐
            "anchors": s["anchors"],
            "photo_type": s.get("photo_type"),
            "wear_level": s.get("wear_level"),
            "watermark": s.get("watermark"),
            "detect_failed": s.get("detect_failed", False),
            "verify_failed": s.get("verify_failed", False),
            "item_texts": s.get("item_texts", []),
            "text_level": s.get("text_level"),
            "item_box": s.get("item_box"),                     # 누끼 비교(그래프 밖)가 원본을 오릴 범위
            "item_count": s.get("item_count"),
            "mode": s.get("mode", "generate"),
            "composite_reason": s.get("composite_reason"),
            "gate_retried": s.get("gate_retried", False),
            "composite_error": s.get("composite_error"),
            "checks": s.get("checks", []),
            "gate_checks": s.get("gate_checks", []),
            "gate_passed": s.get("gate_passed"),
            "visual_similarity": s.get("visual_similarity"),   # ⭐
            "item_similarity": s.get("item_similarity"),
            "item_patch_similarity": s.get("item_patch_similarity"),
            "ocr_local_recall": s.get("ocr_local_recall"),
            "gen_attempts": s.get("gen_attempts"),             # ⭐
            "photo_check": s.get("photo_check"),               # ⭐
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
    원본을 내보냈으면(mode=original) 호출부가 부르지 않는다. 실패는 모두 삼킨다 (관측 신호)."""
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


ITEM_SIGNAL_MODES = ("generate", "composite_failed")   # 결과가 생성본일 때만 — 합성본·원본은 물건 픽셀이 원본


def item_signals_and_save(file_id: str, preset_key: str, *, trace_id: str | None = None,
                          parent_span_id: str | None = None) -> None:
    """누끼 비교(item_dino·item_patch) → inspect JSON 에 채운다. judge_and_save 와 같은 자리에서
    부른다 (라우트는 응답 뒤, 그 외는 그래프 직후). 저장본(정규화 후)을 오린다 — 예전 그래프 안
    계산은 fal 이 준 정규화 전 바이트였다 (값이 소폭 다를 수 있음).
    계산 중에 같은 쌍이 다시 변환되면(결과 이미지·inspect 가 바뀜) 옛 값을 쓰지 않는다. 실패는 모두 삼킨다."""
    name = f"{file_id}_{preset_key}"
    inspect_name = f"{name}_inspect.json"
    try:
        ins_bytes = storage.load("quality", inspect_name)
        original = storage.load_original(file_id)
        result = storage.load("result", f"{name}.jpg")
        if ins_bytes is None or original is None or result is None:
            return
        ins = json.loads(ins_bytes)
        if ins.get("mode") not in ITEM_SIGNAL_MODES:
            return
        with observe("item_signals", as_type="span", trace_id=trace_id,
                     parent_span_id=parent_span_id,
                     input={"file_id": file_id, "preset_key": preset_key}):
            g, p = _item_signals({"original": original, "result": result,
                                  "item_box": ins.get("item_box")})
            if (storage.load("result", f"{name}.jpg") != result
                    or storage.load("quality", inspect_name) != ins_bytes):
                print("[guards] 누끼 비교 중 결과가 바뀜 → 버림")
                return
            ins["item_similarity"] = g.value if g else None
            ins["item_patch_similarity"] = p.value if p else None
            ins["guard_report"] = (ins.get("guard_report") or []) + [
                asdict(x) for x in (g, p) if x is not None and not x.passed]
            storage.save("quality", inspect_name,
                         json.dumps(ins, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception as e:
        print(f"[guards] 누끼 비교 실패(무시): {e}")
    finally:
        flush()


def _inline_item_signals(file_id: str, preset_key: str, result: dict) -> None:
    """그래프 직후 바로 누끼 비교 — 결과 dict 의 값도 inspect 에 맞춘다 (eval·dev 호출부가 읽는다)."""
    item_signals_and_save(file_id, preset_key)
    ins = json.loads(storage.load("quality", f"{file_id}_{preset_key}_inspect.json") or b"{}")
    result["item_similarity"] = ins.get("item_similarity")
    if "guard_report" in result:
        result["guard_report"] = ins.get("guard_report", result["guard_report"])


def _record_result_safe(file_id, preset_key, result_name, item, considered,
                         gate_passed, bubbles, elapsed_s, route=None):
    """DB 기록은 detect/verify/judge와 같은 원칙 — 실패해도 이미 끝난(비용 든)
    변환 자체를 실패로 되돌리지 않는다. route = 결과의 mode·composite_reason·photo_type·wear_level."""
    try:
        store.record_result(file_id, preset_key, result_name, item, considered,
                             gate_passed, bubbles, elapsed_s=elapsed_s, route=route)
    except Exception as e:
        print(f"[record_result] 실패(무시): {e}")


def _route_of(out: dict) -> dict:
    """DB results 에 남길 경로 정보."""
    return {k: out.get(k) for k in ("mode", "composite_reason", "photo_type", "wear_level")}


def finalize(s: State) -> dict:
    checks = s.get("checks", [])
    bubbles = detector.bubbles(checks)
    logger.info(f"[analyze] item={s.get('item')} anchors={len(s['anchors'])} "
                f"photo_type={s.get('photo_type')} wear={s.get('wear_level')} "
                f"watermark={s.get('watermark')} text={s.get('text_level')}")
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
    앞단(analyze → 글자 읽기 여부)과 뒷단 노드는 운영 그래프와 같은 함수·같은 연결을 쓴다
    — verify 프롬프트 튜닝 결과가 운영과 어긋나지 않게 (judge 는 그래프 밖, 같은 함수)."""
    g = StateGraph(State)
    nodes = [("load", load), ("analyze", analyze),
             ("read_text", read_text), ("plan", plan),
             ("score_similarity", score_similarity), ("verify", verify),
             ("save_inspect", save_inspect),
             ("finalize", finalize)]
    nodes += ([("use_provided", use_provided)] if dev else
              [("generate", generate), ("validate_result", validate_result),
               ("mark_gate_retry", mark_gate_retry), ("composite", composite),
               ("keep_original", keep_original)])
    for n, f in nodes:
        g.add_node(n, f)

    g.add_edge(START, "load")
    g.add_edge("load", "analyze")
    # analyze 가 먼저 — 그 text_level 로 글자 읽기(VLM 1회)를 할지 정한다. 글자 없는 물건(대부분)은
    # 읽기가 통째로 빠진다. analyze 는 예전 classify + detect (VLM 2회 → 1회)
    g.add_edge("analyze", "plan")

    if dev:
        g.add_conditional_edges("plan", _route_after_plan_dev,
                                {"read_text": "read_text", "use_provided": "use_provided"})
        g.add_edge("read_text", "use_provided")
        g.add_edge("use_provided", "score_similarity")
        g.add_edge("score_similarity", "verify")
        g.add_edge("verify", "save_inspect")
    else:
        g.add_conditional_edges("plan", _route_after_plan,
                                {"generate": "generate", "composite": "composite",
                                 "read_text": "read_text", "keep_original": "keep_original"})
        g.add_conditional_edges("read_text", _route_after_read_text,
                                {"generate": "generate", "composite": "composite"})
        g.add_edge("generate", "validate_result")
        g.add_conditional_edges("validate_result", _route_after_validate,
                                {"retry": "generate", "ok": "score_similarity"})
        g.add_edge("score_similarity", "verify")
        g.add_conditional_edges("verify", _route_after_verify,
                                {"done": "save_inspect", "regen": "mark_gate_retry",
                                 "composite": "composite"})
        g.add_edge("mark_gate_retry", "generate")
        g.add_edge("keep_original", "save_inspect")
        g.add_conditional_edges("composite", _route_after_composite,
                                {"ok": "score_similarity", "failed": "save_inspect",
                                 "generate": "generate", "read_text": "read_text"})
    g.add_edge("save_inspect", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


GRAPH = build()

# 최악 경로 = 앞 4단계(load·analyze·plan·read_text) + composite(생성 전, 실패)
# + (generate·validate) × 재생성 한도 × 2(게이트 재생성)
# + score/verify 3회 + composite + 뒷 2노드 ≈ 35. max_generate_attempts=5 여도 넉넉하게.
# (LangGraph 기본 25 는 기본 설정에서도 경계라, 비용을 다 쓴 뒤 예외로 끝날 수 있었다)
RECURSION_LIMIT = 80


def run_transform(file_id: str, preset_key: str, *, defer_judge: bool = False,
                  note: str = "", composition: str | None = None) -> dict:
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
                "guard_report": [], "mode": "generate"}

    try:
        with observe("transform", as_type="span",
                     input={"file_id": file_id, "preset_key": preset_key},
                     metadata={"pipeline_mode": settings.pipeline_mode}) as obs:
            # 옛 성적표는 시작할 때 지운다 — 파일명이 file_id/preset 뿐이라 남겨 두면
            # 새 결과에 옛 점수가 붙는다 (원본 그대로·채점 실패 때도)
            _clear_quality(f"{file_id}_{preset_key}.json")
            out = GRAPH.invoke({"file_id": file_id, "preset_key": preset_key, "style_note": note,
                                "composition": composition},
                               {"recursion_limit": RECURSION_LIMIT})
            # 그래프 도중(새 결과 저장 전)에 이전 요청의 백그라운드 채점이 옛 결과 점수를
            # 저장했을 수 있다 — 새 결과가 저장된 뒤 한 번 더 지운다. 이후에 그 채점이
            # 저장하려 하면 judge_and_save 의 재확인(결과 바뀜)이 스스로 지운다.
            _clear_quality(f"{file_id}_{preset_key}.json")
            # 원본을 내보내면(mode=original: inside_view · 오리기 실패한 문서·하자 heavy) 채점 안 함
            judged = out.get("mode") != "original"
            if judged and not defer_judge:
                judge_and_save(file_id, preset_key)
            item_pending = out.get("mode", "generate") in ITEM_SIGNAL_MODES

            result = {
                "result_name": out["result_name"],
                "prompt_used": out["prompt_used"],
                "checks": out["checks"],
                "bubbles": out["bubbles"],
                "gate_passed": out["gate_passed"],
                "item": out.get("item", "object"),
                "considered": out.get("considered", []),
                "visual_similarity": out.get("visual_similarity"),
                "item_similarity": out.get("item_similarity"),
                "gen_attempts": out.get("gen_attempts"),
                "photo_check": out.get("photo_check"),
                "guard_report": out.get("guard_report", []),
                "mode": out.get("mode", "generate"),
                "composite_reason": out.get("composite_reason"),
                "photo_type": out.get("photo_type"),
                "wear_level": out.get("wear_level"),
                "watermark": out.get("watermark"),
                "detect_failed": out.get("detect_failed", False),
                "verify_failed": out.get("verify_failed", False),
                "judge_pending": defer_judge and judged,
                "item_signals_pending": defer_judge and item_pending,
                "trace_id": current_trace_id(),
                "trace_span_id": getattr(obs, "id", None),
            }

            if item_pending and not defer_judge:
                _inline_item_signals(file_id, preset_key, result)
            if obs is not None:
                obs.update(output={
                    "gate_passed": result["gate_passed"],
                    "bubbles": len(result["bubbles"]),
                    "item": result["item"],
                    "visual_similarity": result["visual_similarity"],
                    "item_similarity": result.get("item_similarity"),
                    "gen_attempts": result["gen_attempts"],
                    "photo_check": result["photo_check"],
                    "mode": result["mode"],
                    "composite_reason": result["composite_reason"],
                    "photo_type": result["photo_type"],
                    "wear_level": result["wear_level"],
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
                         elapsed_s, route=_route_of(result))
    return result


def run_transform_with_result(file_id: str, preset_key: str, result_bytes: bytes) -> dict:
    """dev 전용: generate() 를 건너뛰고 주어진 결과 이미지로 이후 단계
    (verify/judge/finalize)만 돌린다 — fal.ai 를 매번 기다리지 않고
    verify/judge 프롬프트를 반복 튜닝하기 위함.

    운영과 같은 노드 함수·같은 앞단 연결(detect → 글자 읽기 여부)을 쓰는 dev 그래프
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
                "item_similarity": s.get("item_similarity"),
                "detect_failed": s.get("detect_failed", False),
                "verify_failed": s.get("verify_failed", False),
                "mode": s.get("mode", "generate"),
                "photo_type": s.get("photo_type"),
                "wear_level": s.get("wear_level"),
                "watermark": s.get("watermark"),
            }
            if result["mode"] in ITEM_SIGNAL_MODES:
                _inline_item_signals(file_id, preset_key, result)
            if obs is not None:
                obs.update(output={
                    "gate_passed": result["gate_passed"],
                    "bubbles": len(result["bubbles"]),
                    "item": result["item"],
                    "visual_similarity": result["visual_similarity"],
                    "item_similarity": result.get("item_similarity"),
                })
    finally:
        flush()
    elapsed_s = time.time() - t0
    logger.info(f"[test] total {elapsed_s:.1f}s (generate skipped)")
    _record_result_safe(file_id, preset_key, result["result_name"], result["item"],
                         result["considered"], result["gate_passed"], result["bubbles"],
                         elapsed_s, route=_route_of(result))
    return result