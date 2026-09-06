"""변환 파이프라인 - LangGraph 판.

스테이지:
  0) load     : 원본 로드
  0.5) classify : 물건 식별 + 루브릭(considered) 수립  ⭐ 신규
  1) detect   : 원본 → 하자 의미 앵커 (what/where)
  2) generate : 배경 교체
  3) verify   : 결과 → 보존 여부 + 결과 좌표 (말풍선용)
  4) judge    : 품질 성적표 (캐시)
  5) finalize : bubbles + 로깅
"""
import json
import logging
import time
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

from app.core.config import settings
from app.prompts.presets import get_preset
from app.services import detector, judge, storage
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
    checks: list
    gate_passed: bool | None
    bubbles: list
    prompt_used: str


# ── 노드 ─────────────────────────────────────────
def load(s: State) -> dict:
    original_path = storage.original_of(s["file_id"])
    if original_path is None:
        raise FileNotFoundError(s["file_id"])
    return {
        "original": original_path.read_bytes(),
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


def verify(s: State) -> dict:
    checks, gate_passed = [], None
    if s["anchors"]:
        try:
            saved = (storage.BASE / "result" / s["result_name"]).read_bytes()
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
        }, ensure_ascii=False, indent=2).encode("utf-8"),
    )
    return {}


def run_judge(s: State) -> dict:
    """품질 성적표 (캐시)."""
    quality_path = (storage.BASE / "quality"
                    / f"{s['file_id']}_{s['preset_key']}.json")
    if not quality_path.exists():
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
                 ("verify", verify), ("save_inspect", save_inspect),
                 ("run_judge", run_judge), ("finalize", finalize)):
        g.add_node(n, f)

    g.add_edge(START, "load")
    g.add_edge("load", "classify")        # ⭐ 신규 단계
    g.add_edge("classify", "detect")
    g.add_edge("detect", "generate")
    g.add_edge("generate", "verify")
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
        original = storage.original_of(file_id)
        if original is None:
            raise FileNotFoundError(file_id)
        result_name = f"{file_id}_{preset_key}.jpg"
        storage.save("result", result_name, original.read_bytes())
        return {"result_name": result_name, "prompt_used": "PASS-THROUGH",
                "checks": [], "bubbles": [], "gate_passed": None,"item": "object", "considered": []}

    out = GRAPH.invoke({"file_id": file_id, "preset_key": preset_key})
    logger.info(f"total {time.time() - t0:.1f}s")
    return {
        "result_name": out["result_name"],
        "prompt_used": out["prompt_used"],
        "checks": out["checks"],
        "bubbles": out["bubbles"],
        "gate_passed": out["gate_passed"],
        "item": out.get("item", "object"),        
        "considered": out.get("considered", []),
    }