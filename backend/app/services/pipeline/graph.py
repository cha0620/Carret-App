"""그래프 조립(build)과 실행 진입점(run_transform)."""
import logging
import time
from langgraph.graph import END, START, StateGraph
from app.core.config import settings
from app.core.tracing import current_trace_id, flush, observe
from app.services.persistence import storage

from app.services.pipeline.state import State
from app.services.pipeline.analysis import analyze, load, read_text
from app.services.pipeline.routing import _route_after_plan, _route_after_plan_dev, _route_after_read_text, plan
from app.services.pipeline.generation import generate
from app.services.pipeline.checks import (
    _route_after_validate,
    _route_after_verify,
    detail_requests,
    mark_gate_retry,
    score_similarity,
    validate_result,
    verify,
)
from app.services.pipeline.composite import _route_after_composite, composite, keep_original
from app.services.pipeline.results import (
    ITEM_SIGNAL_MODES,
    _clear_quality,
    _inline_item_signals,
    _record_result_safe,
    _route_of,
    finalize,
    judge_and_save,
    save_inspect,
    use_provided,
)

logger = logging.getLogger("carret.pipeline")


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


def run_transform(file_id: str, preset_key: str, *, defer_signals: bool = False,
                  score_quality: bool = False, note: str = "", composition: str | None = None,
                  sell: list | None = None, answer_count: int | None = None,
                  extra_view_ids: list | None = None, ref_category: str | None = None,
                  ref_view: str | None = None, ref_file: str | None = None,
                  ref_layout: str | None = None, ref_sketch: bool = False,
                  clean_prompt: bool | str = False, ref_keep: str | None = None,
                  extra_views_explicit: bool = False, mains: list | None = None,
                  fixed_pieces: list | None = None, user_mode: str | None = None) -> dict:
    """defer_signals=True: 누끼 비교를 하지 않고 결과에 item_signals_pending/trace_id 를 실어 보낸다 —
    호출부(transform 라우트)가 응답 뒤 item_signals_and_save() 를 돌린다.
    score_quality=True: 그래프 직후 judge 성적표를 채점 (eval · dev 만 — 운영 경로는 안 부른다)."""
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
                             None, elapsed_s=time.time() - t0)
        return {"result_name": result_name, "prompt_used": "PASS-THROUGH",
                "checks": [], "gate_passed": None, "item": "object", "considered": [],
                "guard_report": [], "mode": "generate"}

    try:
        with observe("transform", as_type="span",
                     input={"file_id": file_id, "preset_key": preset_key},
                     metadata={"pipeline_mode": settings.pipeline_mode}) as obs:
            # 옛 성적표(eval · dev 가 남긴 것)는 시작할 때 지운다 — 파일명이 file_id/preset 뿐이라
            # 남겨 두면 새 결과에 옛 점수가 붙는다 (원본 그대로·채점 실패 때도)
            _clear_quality(f"{file_id}_{preset_key}.json")
            out = GRAPH.invoke({"file_id": file_id, "preset_key": preset_key, "style_note": note,
                                "composition": composition, "sell": sell, "mains": mains, "answer_count": answer_count,
                                "extra_view_ids": extra_view_ids or [],
                                "extra_views_explicit": extra_views_explicit,
                                "ref_category": ref_category, "ref_view": ref_view, "ref_file": ref_file,
                                "ref_layout": ref_layout, "ref_sketch": ref_sketch,
                                "clean_prompt": clean_prompt, "ref_keep": ref_keep,
                                "fixed_pieces": fixed_pieces or [], "user_mode": user_mode},
                               {"recursion_limit": RECURSION_LIMIT})
            # 채점할 때만: 그래프 도중에 같은 쌍을 돌던 다른 eval · dev 채점이 옛 점수를 저장했을 수
            # 있다 — 새로 채점하기 전에 한 번 더 지운다 (운영은 성적표를 만들지 않아 삭제 왕복을 아낀다).
            # 원본을 내보내면(mode=original: inside_view · 오리기 실패한 문서·하자 heavy) 채점 안 함
            if score_quality:
                _clear_quality(f"{file_id}_{preset_key}.json")
                if out.get("mode") != "original":
                    judge_and_save(file_id, preset_key)
            item_pending = out.get("mode", "generate") in ITEM_SIGNAL_MODES

            result = {
                "result_name": out["result_name"],
                "prompt_used": out["prompt_used"],
                "gen_prompts": out.get("gen_prompts") or [],
                "count_mismatch": out.get("count_mismatch"),
                "checks": out["checks"],
                "gate_passed": out["gate_passed"],
                "item": out.get("item", "object"),
                "considered": out.get("considered", []),
                "detail_pieces": out.get("detail_pieces") or [],   # 대표사진에서 뺀 구성품 — 상세 · 인증 (10-10)
                "visual_similarity": out.get("visual_similarity"),
                "item_similarity": out.get("item_similarity"),
                "gen_attempts": out.get("gen_attempts"),
                "photo_check": out.get("photo_check"),
                "guard_report": out.get("guard_report", []),
                "mode": out.get("mode", "generate"),
                "composite_reason": out.get("composite_reason"),
                "risk_reason": out.get("risk_reason"),
                "detail_requests": detail_requests(out),
                "photo_type": out.get("photo_type"),
                "wear_level": out.get("wear_level"),
                "watermark": out.get("watermark"),
                "detect_failed": out.get("detect_failed", False),
                "verify_failed": out.get("verify_failed", False),
                "item_signals_pending": defer_signals and item_pending,
                "trace_id": current_trace_id(),
                "trace_span_id": getattr(obs, "id", None),
            }

            if item_pending and not defer_signals:
                _inline_item_signals(file_id, preset_key, result)
            if obs is not None:
                obs.update(output={
                    "gate_passed": result["gate_passed"],
                    "item": result["item"],
                    "visual_similarity": result["visual_similarity"],
                    "item_similarity": result.get("item_similarity"),
                    "gen_attempts": result["gen_attempts"],
                    "photo_check": result["photo_check"],
                    "mode": result["mode"],
                    "composite_reason": result["composite_reason"],
                    "risk_reason": result["risk_reason"],
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
                         result["considered"], result["gate_passed"],
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
                    "item": result["item"],
                    "visual_similarity": result["visual_similarity"],
                    "item_similarity": result.get("item_similarity"),
                })
    finally:
        flush()
    elapsed_s = time.time() - t0
    logger.info(f"[test] total {elapsed_s:.1f}s (generate skipped)")
    _record_result_safe(file_id, preset_key, result["result_name"], result["item"],
                         result["considered"], result["gate_passed"],
                         elapsed_s, route=_route_of(result))
    return result
