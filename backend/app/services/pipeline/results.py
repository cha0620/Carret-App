"""기록 — inspect 저장, 성적표(judge, eval · dev 만), 누끼 비교, DB 기록, finalize."""
import logging
import json
from dataclasses import asdict
from app.core.tracing import flush, observe, score
from app.prompts.rubric import AXES
from app.services.ai import judge
from app.services.persistence import storage, store

from app.services.pipeline.state import State
from app.services.pipeline.checks import _item_signals

logger = logging.getLogger("carret.pipeline")


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
            "added_text": s.get("added_text") or [], "gate_added_text": s.get("gate_added_text") or [],
            "sell": s.get("sell"), "leave_out": s.get("leave_out") or [],
            "mode": s.get("mode", "generate"),
            "composite_reason": s.get("composite_reason"),
            "user_mode": s.get("user_mode"),
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
        logger.warning(f"[judge] 옛 성적표 삭제 실패(무시): {e}")


def judge_and_save(file_id: str, preset_key: str, *, trace_id: str | None = None,
                   parent_span_id: str | None = None) -> None:
    """품질 성적표 채점 → 저장 → 축별 점수 부착. 채점 구현은 여기 하나뿐이다.

    그래프 밖에서 부른다 — 채점 결과를 기다려야 하는 단계가 그래프에 없다
    (judge 는 결정에 안 쓰이는 관측 신호). 10-05 부터 운영(transform 라우트)은 부르지 않고
    eval · dev 만 그래프 직후 바로 부른다: run_transform(score_quality=True) ·
    run_transform_with_result (현재 트레이스에 자동 중첩). trace_id/parent_span_id 는
    요청 컨텍스트 밖(백그라운드)에서 부를 때 같은 트레이스에 이어 붙이는 용도.
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
                logger.info("[judge] 채점 중 결과가 바뀜 → 버림")
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
                logger.warning(f"[judge] 점수 부착 실패(무시): {e}")
    except Exception as e:
        logger.warning(f"[judge] 실패(무시): {e}")
    finally:
        flush()


ITEM_SIGNAL_MODES = ("generate", "composite_failed")   # 결과가 생성본일 때만 — 합성본·원본은 물건 픽셀이 원본


def item_signals_and_save(file_id: str, preset_key: str, *, trace_id: str | None = None,
                          parent_span_id: str | None = None) -> None:
    """누끼 비교(item_dino·item_patch) → inspect JSON 에 채운다. transform 라우트는 응답 뒤
    백그라운드로, 그 외는 그래프 직후 부른다. 저장본(정규화 후)을 오린다 — 예전 그래프 안
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
                logger.info("[guards] 누끼 비교 중 결과가 바뀜 → 버림")
                return
            ins["item_similarity"] = g.value if g else None
            ins["item_patch_similarity"] = p.value if p else None
            ins["guard_report"] = (ins.get("guard_report") or []) + [
                asdict(x) for x in (g, p) if x is not None and not x.passed]
            storage.save("quality", inspect_name,
                         json.dumps(ins, ensure_ascii=False, indent=2).encode("utf-8"))
    except Exception as e:
        logger.warning(f"[guards] 누끼 비교 실패(무시): {e}")
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
                         gate_passed, elapsed_s, route=None):
    """DB 기록은 detect/verify/judge와 같은 원칙 — 실패해도 이미 끝난(비용 든)
    변환 자체를 실패로 되돌리지 않는다. route = 결과의 mode·composite_reason·photo_type·wear_level."""
    try:
        store.record_result(file_id, preset_key, result_name, item, considered,
                             gate_passed, elapsed_s=elapsed_s, route=route)
    except Exception as e:
        logger.warning(f"[record_result] 실패(무시): {e}")


def _route_of(out: dict) -> dict:
    """DB results 에 남길 경로 정보."""
    return {k: out.get(k) for k in ("mode", "composite_reason", "photo_type", "wear_level")}


def finalize(s: State) -> dict:
    checks = s.get("checks", [])
    logger.info(f"[analyze] item={s.get('item')} anchors={len(s['anchors'])} "
                f"photo_type={s.get('photo_type')} wear={s.get('wear_level')} "
                f"watermark={s.get('watermark')} text={s.get('text_level')}")
    logger.info(f"[verify] preserved="
                f"{sum(c['preserved'] for c in checks)}/{len(checks)}")
    logger.info(f"[validate_result] attempts={s.get('gen_attempts')} "
                f"photo_check={s.get('photo_check')}")
    logger.info(f"transform {s['file_id']}/{s['preset_key']} "
                f"gate={s.get('gate_passed')}")
    return {}


def use_provided(s: State) -> dict:
    """dev 그래프 전용: generate 대신 주어진 결과 이미지를 쓴다."""
    storage.save("result", s["result_name"], s["provided_result"])   # 실제 경로와 동일하게 정규화됨
    return {"result": storage.load("result", s["result_name"]),
            "prompt_used": "TEST: provided result (generate skipped)"}
