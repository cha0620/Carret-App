"""변환 파이프라인 - LangGraph 판.

스테이지:
  0) load     : 원본 로드
  0.5) classify : 물건 식별 + 루브릭(considered) 수립  ⭐ 신규
  1) detect   : 원본 → 하자 의미 앵커 (what/where)
  2) generate : 배경 교체
  2.3) validate_result : ① 출력 가드(guards.py) — hard fail 이면 같은 파라미터 +
       seed 만 바꿔 generate 1회 재시도, 그래도 fail 이면 status="blocked"
       (결과 대신 원본을 내보냄). ② 결과가 '제대로 된 사진'인지 (구도/자막
       오버레이) — invalid면 max_generate_attempts 까지 generate 를 재시도
  2.5) score_similarity : 원본 vs 결과 DINOv2 코사인 유사도 (벡터 점수)  ⭐ 신규
  3) verify   : 결과 → 보존 여부 + 결과 좌표 (말풍선용)
  4) judge    : 품질 성적표 (캐시) — fidelity/realism/trust를 Langfuse 트레이스에도 점수로 부착
  5) finalize : bubbles + 로깅
"""

import json
import logging
import random
import time
from dataclasses import asdict
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.core.tracing import flush, observe, score
from app.prompts.presets import get_preset
from app.prompts.rubric import AXES
from app.services.ai import detector, embedder, judge
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


def detect(s: State) -> dict:
    try:
        anchors = detector.detect_defects(
            s["original"], s.get("item", "object"), s.get("considered", []))
    except Exception as e:
        print(f"[detect] 실패(무시): {e}")
        anchors = []
    return {"anchors": anchors}


def generate(s: State) -> dict:
    preset = s["preset"]
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
    result_name = f"{s['file_id']}_{s['preset_key']}.jpg"
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
    }


def _guard_anchors(anchors: list) -> list:
    """run_output_guards 는 box 가 있는 앵커만 다룬다 — detect 앵커(what/where)는
    아직 box 가 없어서 지금은 전부 걸러지고 ocr/dino 가드만 돈다."""
    return [a for a in anchors if "box" in a]


def _run_guards(s: State) -> tuple[str, list]:
    # OCR 은 아직 미연결(T3) — 빈 리스트. 가드 계산 예외는 삼키지 않는다
    # (guards.py 설계: 최소선 gate 가 fail-open 되면 안 됨).
    results = guards.run_output_guards(
        s["original"], s["result"], _guard_anchors(s.get("anchors", [])), [], [])
    verdict, _ = guards.decide(results)
    return verdict, [asdict(g) for g in results if not g.passed]


def validate_result(s: State) -> dict:
    """① 출력 가드 → ② 결과가 '제대로 된 사진'인지 VLM 으로 확인 (구도가
    잘렸거나 자막/텍스트가 상품을 가리면 invalid). 라우팅(_route_after_validate)이
    이 결과를 보고 generate 로 되돌릴지 결정한다."""
    verdict, report = _run_guards(s)
    if verdict == "block":
        if s.get("guard_seed") is None:
            seed = random.randint(0, 2**32 - 1)
            print(f"[guards] hard fail, seed={seed} 로 1회 재시도: "
                  f"{[g['name'] for g in report]}")
            return {"guard_retry": True, "guard_seed": seed,
                    "guard_report": report}
        # 재시도도 fail → 변환 결과는 내보내지 않고 원본으로 덮는다 (침묵 대신 고지).
        print(f"[guards] 재시도도 hard fail → blocked: {[g['name'] for g in report]}")
        storage.save("result", s["result_name"], s["original"])
        return {"guard_retry": False, "status": "blocked",
                "guard_report": report, "result": s["original"],
                "photo_check": None}   # 내보내는 건 원본 — 생성 이미지 검사 결과는 무의미

    storage.save("result", s["result_name"], s["result"])
    try:
        check = detector.check_photo(s["result"])
    except Exception as e:
        print(f"[validate_result] 실패(무시): {e}")
        check = {"valid": True, "reason": ""}
    return {"photo_check": check, "guard_retry": False, "status": "pass",
            "guard_report": report}


def _route_after_validate(s: State) -> str:
    if s.get("guard_retry"):
        return "retry"
    if s.get("status") == "blocked":
        return "ok"
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
    try:
        sim = embedder.cosine_similarity(s["original"], s["result"])
    except Exception as e:
        print(f"[score_similarity] 실패(무시): {e}")
        return {"visual_similarity": None}
    score("visual_similarity", sim, data_type="NUMERIC")
    return {"visual_similarity": sim}


def verify(s: State) -> dict:
    checks, gate_passed = [], None
    if s["anchors"] and s.get("status") != "blocked":
        try:
            saved = storage.load("result", s["result_name"])
            checks = detector.verify_and_locate(
                saved, s["anchors"],
                s.get("item", "object"), s.get("considered", []))
            gate_passed = detector.all_preserved(checks, expected=len(s["anchors"]))
        except Exception as e:
            print(f"[verify] 실패(무시): {e}")
    return {"checks": checks, "gate_passed": gate_passed}


def save_inspect(s: State) -> dict:
    """디버그 인스펙트 (classify 흔적 포함 = 투명성)."""
    storage.save(
        "quality",
        f"{s['file_id']}_{s['preset_key']}_inspect.json",
        json.dumps({
            "item": s.get("item", "object"),          # ⭐
            "considered": s.get("considered", []),    # ⭐
            "anchors": s["anchors"],
            "checks": s["checks"],
            "gate_passed": s["gate_passed"],
            "visual_similarity": s.get("visual_similarity"),   # ⭐
            "gen_attempts": s.get("gen_attempts"),             # ⭐
            "photo_check": s.get("photo_check"),               # ⭐
            "status": s.get("status", "pass"),
            "guard_seed": s.get("guard_seed"),
            "guard_report": s.get("guard_report", []),
        }, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return {}


def run_judge(s: State) -> dict:
    """품질 성적표 (캐시) — 새로 계산한 경우 Langfuse 트레이스에 축별 점수도 부착."""
    quality_name = f"{s['file_id']}_{s['preset_key']}.json"
    if s.get("status") == "blocked":
        return {}
    if not storage.exists("quality", quality_name):
        try:
            report = judge.judge(s["original"], s["result"])
        except Exception as e:
            print(f"[judge] 실패(무시): {e}")
            report = None
        if report:
            storage.save(
                "quality", f"{s['file_id']}_{s['preset_key']}.json",
                json.dumps(report, ensure_ascii=False,
                           indent=2).encode("utf-8"),
            )
            try:
                for axis in AXES:
                    score(axis, report[axis], data_type="NUMERIC")
            except Exception as e:
                print(f"[judge] 점수 부착 실패(무시): {e}")
    return {}


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
    bubbles = detector.bubbles(s["checks"])
    logger.info(f"[classify] item={s.get('item')} "
                f"considered={len(s.get('considered', []))}")
    logger.info(f"[detect] anchors={len(s['anchors'])}")
    logger.info(f"[verify] preserved="
                f"{sum(c['preserved'] for c in s['checks'])}/{len(s['checks'])}")
    logger.info(f"[validate_result] attempts={s.get('gen_attempts')} "
                f"photo_check={s.get('photo_check')}")
    logger.info(f"transform {s['file_id']}/{s['preset_key']} "
                f"bubbles={bubbles} gate={s['gate_passed']}")
    return {"bubbles": bubbles}


# ── 조립 ─────────────────────────────────────────
def build():
    g = StateGraph(State)
    for n, f in (("load", load), ("classify", classify_node),
                 ("detect", detect), ("generate", generate),
                 ("validate_result", validate_result),
                 ("score_similarity", score_similarity),
                 ("verify", verify), ("save_inspect", save_inspect),
                 ("run_judge", run_judge), ("finalize", finalize)):
        g.add_node(n, f)

    g.add_edge(START, "load")
    g.add_edge("load", "classify")        # ⭐ 신규 단계
    g.add_edge("classify", "detect")
    g.add_edge("detect", "generate")
    g.add_edge("generate", "validate_result")    # ⭐ 신규 단계
    g.add_conditional_edges("validate_result", _route_after_validate,
                             {"retry": "generate", "ok": "score_similarity"})
    g.add_edge("score_similarity", "verify")
    g.add_edge("verify", "save_inspect")
    g.add_edge("save_inspect", "run_judge")
    g.add_edge("run_judge", "finalize")
    g.add_edge("finalize", END)
    return g.compile()


GRAPH = build()


def run_transform(file_id: str, preset_key: str) -> dict:
    t0 = time.time()

    # pass 모드 = 지름길 (그래프 안 탐)
    if settings.pipeline_mode == "mock":
        original = storage.load_original(file_id)
        if original is None:
            raise FileNotFoundError(file_id)
        result_name = f"{file_id}_{preset_key}.jpg"
        storage.save("result", result_name, original)
        _record_result_safe(file_id, preset_key, result_name, "object", [],
                             None, [], elapsed_s=time.time() - t0)
        return {"result_name": result_name, "prompt_used": "PASS-THROUGH",
                "checks": [], "bubbles": [], "gate_passed": None, "item": "object", "considered": [],
                "status": "pass", "guard_report": []}

    try:
        with observe("transform", as_type="span",
                     input={"file_id": file_id, "preset_key": preset_key},
                     metadata={"pipeline_mode": settings.pipeline_mode}) as obs:
            out = GRAPH.invoke({"file_id": file_id, "preset_key": preset_key})
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
            }
            if obs is not None:
                obs.update(output={
                    "gate_passed": result["gate_passed"],
                    "bubbles": len(result["bubbles"]),
                    "item": result["item"],
                    "visual_similarity": result["visual_similarity"],
                    "gen_attempts": result["gen_attempts"],
                    "photo_check": result["photo_check"],
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

    노드 함수는 run_transform 과 완전히 동일한 것을 그대로 호출한다
    (로직 복사 금지: 테스트는 프로덕션을 호출하지, 베끼지 않는다).
    """
    t0 = time.time()
    try:
        with observe("transform_dev", as_type="span",
                     input={"file_id": file_id, "preset_key": preset_key},
                     metadata={"generate_skipped": True}) as obs:
            s: State = {"file_id": file_id, "preset_key": preset_key}
            s.update(load(s))
            s.update(classify_node(s))
            s.update(detect(s))

            result_name = f"{file_id}_{preset_key}.jpg"
            storage.save("result", result_name, result_bytes)   # 실제 경로와 동일하게 정규화됨
            s["result"] = result_bytes
            s["result_name"] = result_name
            s["prompt_used"] = "TEST: provided result (generate skipped)"

            s.update(score_similarity(s))
            s.update(verify(s))
            s.update(save_inspect(s))
            s.update(run_judge(s))
            s.update(finalize(s))

            result = {
                "result_name": s["result_name"],
                "prompt_used": s["prompt_used"],
                "checks": s["checks"],
                "bubbles": s["bubbles"],
                "gate_passed": s["gate_passed"],
                "item": s.get("item", "object"),
                "considered": s.get("considered", []),
                "visual_similarity": s.get("visual_similarity"),
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