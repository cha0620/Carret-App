"""배경 교체 · 원본 그대로 — 물건 픽셀을 원본으로 쓰는 경로."""
import logging
from app.core.config import settings
from app.services.ai import compositor
from app.services.persistence import storage

from app.services.pipeline.state import State

logger = logging.getLogger("carret.pipeline")


def _original_as_result(s: State) -> dict:
    """원본을 결과로 내보낼 때 뒷단(save_inspect·finalize·run_transform)이 읽는 값을 한 곳에서 채운다."""
    storage.save("result", s["result_name"], s["original"])
    return {"result": s["original"], "mode": "original",
            "checks": [], "gate_passed": None, "photo_check": None, "verify_failed": False,
            "guard_report": [], "prompt_used": "ORIGINAL: background not replaced"}


def keep_original(s: State) -> dict:
    """원본 그대로 (plan: inside_view) — 물건 일부·내부 사진은 생성도 오리기도 하지 않는다.
    물건 픽셀도 배경도 원본이라 검사할 것이 없다 (verify·judge 생략)."""
    logger.info("[keep_original] 물건 일부·내부 사진 → 원본 그대로")
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
                                     drop=s.get("leave_out_boxes"), keep=s.get("sell_boxes"),
                                     flat=reason == "document")
    except Exception as e:
        if s.get("result") is None and reason in ("wear_heavy", "document", "cut_off", "overlap", "multi_item"):
            # 하자가 넓어 "생성하면 지우거나 지어낸다"고 본 사진 / 책·음반처럼 한 글자만 바뀌어도
            # 다른 물건 — 오리기가 안 되면 생성하지 않고 원본을 그대로 보여준다
            # (생성으로 가면 하자·잔글씨 검사 없이 "보존됨" 배지가 붙는다)
            logger.warning(f"[composite] 실패 + {reason} → 원본 그대로: {e}")
            return {**_original_as_result(s), "composite_error": str(e), "composite_reason": reason}
        if s.get("result") is None and s.get("user_mode") == "composite":
            # 배경 교체만 (10-11) — 오리기가 안 되면 생성으로 넘어가지 않고 원본 그대로
            logger.warning(f"[composite] 실패 (배경 교체만) → 원본 그대로: {e}")
            return {**_original_as_result(s), "composite_error": str(e), "composite_reason": reason}
        if s.get("result") is None:
            # 생성 전에 왔다(plan) — 이제 정상 생성 경로로
            logger.warning(f"[composite] 실패, 생성으로 진행: {e}")
            return {"mode": "generate", "composite_error": str(e), "composite_reason": None}
        # 오리기 실패 — 마지막 생성 결과를 그대로 두고 (게이트 실패 기록은 남음) 끝낸다
        logger.warning(f"[composite] 실패, 생성 결과 유지: {e}")
        return {"mode": "composite_failed", "composite_error": str(e)}
    logger.info(f"[composite] 배경 교체 모드로 전환 ({reason})")
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
