"""배경 교체 · 원본 그대로 — 물건 픽셀을 원본으로 쓰는 경로."""
import logging
from app.services.ai import compositor
from app.services.persistence import storage

from app.services.pipeline import progress
from app.services.pipeline.state import State

logger = logging.getLogger("carret.pipeline")


def _original_as_result(s: State) -> dict:
    """원본을 결과로 내보낼 때 뒷단(save_inspect·finalize·run_transform)이 읽는 값을 한 곳에서 채운다."""
    storage.save("result", s["result_name"], s["original"])
    return {"result": s["original"], "mode": "original",
            "checks": [], "gate_passed": None, "photo_check": None, "verify_failed": False,
            "guard_report": [], "prompt_used": "ORIGINAL: background not replaced"}


def composite(s: State) -> dict:
    """배경 교체 모드: 원본 물건을 오려 프리셋 배경 위에 합성 (물건 픽셀 보존).

    들어오는 길: plan — 판매자가 [배경만 바꾸기]를 골랐을 때만 (10-11). 오리기가 안 되면 생성으로 넘어가지 않고 원본 그대로."""
    progress.publish(s.get("job_id"), "composite", clear_preview=True)   # 검사에 떨어진 생성본은 내린다
    reason = s.get("composite_reason") or "user_choice"
    doc = s.get("photo_type") == "document"   # 10-11: 사유가 아니라 분석의 사진 유형으로 본다 (생성 전 판단은 지웠다)
    try:
        # 문서(보증서·영수증)는 정면으로 펴서 놓는다 (못 펴면 compose_flat 이 일반 배경 교체로).
        # 하자가 넓은 문서(찢김·접힘)는 펴지 않는다 — 네 모서리에 맞추면 찢어진 모서리가 잘리고
        # 접힌 자국이 펴져 상태가 좋아 보인다
        # 여러 개(종이 여러 장)도 펴지 않는다 — 붙어 있으면 한 사각형으로 합쳐 펴거나,
        # 한 개만 펴고 나머지를 지운다 (09-29 CD 2장: 한 장이 사라짐)
        if doc and s.get("wear_level") != "heavy" and (s.get("item_count") or 1) <= 1:
            out = compositor.compose_flat(s["original"], s["preset"]["bg_color"], s.get("item_box"))
        else:
            # 문서는 펴지 않아도(여러 개·하자 heavy) 납작한 인쇄물 — 윤곽에 붙은 가는 조각을 뗀다
            out = compositor.compose(s["original"], s["preset"]["bg_color"], s.get("item_box"),
                                     drop=s.get("leave_out_boxes"), keep=s.get("sell_boxes"), flat=doc)
    except Exception as e:
        logger.warning(f"[composite] 실패 → 원본 그대로: {e}")
        return {**_original_as_result(s), "composite_error": str(e), "composite_reason": reason}
    logger.info(f"[composite] 배경 교체 ({reason})")
    storage.save("result", s["result_name"], out)
    # 합성본은 생성본이 아니다 — 생성본 기준 검사 결과(구도 검사·가드·유사도)를 들고 가지 않는다
    return {"result": out, "mode": "composite", "composite_error": None,
            "composite_reason": reason, "photo_check": None,
            "guard_report": [], "visual_similarity": None, "item_similarity": None,
            "item_patch_similarity": None, "ocr_local_recall": None,
            "prompt_used": "COMPOSITE: original item pixels on preset background"}


def _route_after_composite(s: State) -> str:
    return "ok" if s.get("mode") == "composite" else "failed"   # 오리기 실패 → 원본 그대로, 검사할 것 없음
