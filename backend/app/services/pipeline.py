"""변환 파이프라인 - LangGraph 판.

스테이지:
  0) load     : 원본 로드
  0.5) classify : 물건 식별 + 루브릭(considered) 수립  ⭐ 신규
  1) detect   : 원본 → 하자 의미 앵커 (what/where)
  2) generate : 배경 교체
  2.5) score_similarity : 원본 vs 결과 DINOv2 코사인 유사도 (벡터 점수)  ⭐ 신규
  3) verify   : 결과 → 보존 여부 + 결과 좌표 (말풍선용)
  4) judge    : 품질 성적표 (캐시) — fidelity/realism/trust를 Langfuse 트레이스에도 점수로 부착
  5) finalize : bubbles + 로깅
"""
import json
import logging
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.core.tracing import flush, observe, score
from app.prompts.presets import get_preset
from app.prompts.rubric import AXES
from app.services import detector, embedder, judge, storage
from app.services.generator import _generate_ai

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
    gen = _generate_ai(s["original"], s["preset"])
    result_name = f"{s['file_id']}_{s['preset_key']}.jpg"
    storage.save("result", result_name, gen)
    return {
        "result": gen,
        "result_name": result_name,
        "prompt_used": s["preset"]["prompt"],
    }


def score_similarity(s: State) -> dict:
    """원본 vs 결과 DINOv2 코사인 유사도 — VLM judge와 별개인 로컬 벡터 점수."""
    try:
        sim = embedder.cosine_similarity(s["original"], s["result"])
    except Exception as e:
        print(f"[score_similarity] 실패(무시): {e}")
        return {"visual_similarity": None}
    score("visual_similarity", sim, data_type="NUMERIC")
    return {"visual_similarity": sim}


def verify(s: State) -> dict:
    checks, gate_passed = [], None
    if s["anchors"]:
        try:
            saved = storage.load("result", s["result_name"])
            checks = detector.verify_and_locate(
                saved, s["anchors"],
                s.get("item", "object"), s.get("considered", []))
            gate_passed = detector.all_preserved(checks)
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
        }, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return {}


def run_judge(s: State) -> dict:
    """품질 성적표 (캐시) — 새로 계산한 경우 Langfuse 트레이스에 축별 점수도 부착."""
    quality_name = f"{s['file_id']}_{s['preset_key']}.json"
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


def finalize(s: State) -> dict:
    bubbles = detector.bubbles(s["checks"])
    logger.info(f"[classify] item={s.get('item')} "
                f"considered={len(s.get('considered', []))}")
    logger.info(f"[detect] anchors={len(s['anchors'])}")
    logger.info(f"[verify] preserved="
                f"{sum(c['preserved'] for c in s['checks'])}/{len(s['checks'])}")
    logger.info(f"transform {s['file_id']}/{s['preset_key']} "
                f"bubbles={bubbles} gate={s['gate_passed']}")
    return {"bubbles": bubbles}


# ── 조립 ─────────────────────────────────────────
def build():
    g = StateGraph(State)
    for n, f in (("load", load), ("classify", classify_node),
                 ("detect", detect), ("generate", generate),
                 ("score_similarity", score_similarity),
                 ("verify", verify), ("save_inspect", save_inspect),
                 ("run_judge", run_judge), ("finalize", finalize)):
        g.add_node(n, f)

    g.add_edge(START, "load")
    g.add_edge("load", "classify")        # ⭐ 신규 단계
    g.add_edge("classify", "detect")
    g.add_edge("detect", "generate")
    g.add_edge("generate", "score_similarity")   # ⭐ 신규 단계
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
        return {"result_name": result_name, "prompt_used": "PASS-THROUGH",
                "checks": [], "bubbles": [], "gate_passed": None,"item": "object", "considered": []}

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
            }
            if obs is not None:
                obs.update(output={
                    "gate_passed": result["gate_passed"],
                    "bubbles": len(result["bubbles"]),
                    "item": result["item"],
                    "visual_similarity": result["visual_similarity"],
                })
    finally:
        # 성공/실패(예외) 상관없이 이번 요청의 트레이스는 즉시 내보낸다 —
        # 실패 트레이스가 배치 주기까지 안 보내지고 프로세스 종료로 유실되는 걸 방지.
        flush()
    logger.info(f"total {time.time() - t0:.1f}s")
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
    logger.info(f"[test] total {time.time() - t0:.1f}s (generate skipped)")
    return result