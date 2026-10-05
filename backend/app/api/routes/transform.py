"""변환 API - HTTP 껍데기.

하는 일 딱 3개:
1. 요청 검증 (스키마가 자동)
2. 파이프라인 호출
3. 에러 → 상태코드 번역
"""
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.prompts.presets import NOTE_MAX_CHARS, PRESETS, PURPOSES, style_key
from app.schemas.image import (AnalyzeRequest, AnalyzeResponse, StylesResponse,
                               TransformRequest, TransformResponse)
from app.services import pipeline
from app.services.persistence import storage


logger = logging.getLogger("carret")
router = APIRouter()


@router.get("/styles", response_model=StylesResponse)
def styles():
    """무드 카드·목적 — 프론트가 이 목록으로 그린다 (무드 정의는 presets.py 한 곳)."""
    return StylesResponse(
        moods=[{"key": k, "name": v["name"], "emoji": v["emoji"],
                "color": "#%02x%02x%02x" % v["bg_color"]} for k, v in PRESETS.items()],
        purposes=[{"key": k, **v} for k, v in PURPOSES.items()],
        note_max_chars=NOTE_MAX_CHARS)


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    """업로드 직후 미리 분석 — 무드를 고르기 전에 "이 사진은 어떻게 처리되는지" 보여준다.
    결과는 저장돼 변환 때 다시 쓴다 (VLM 을 두 번 부르지 않는다)."""
    original = storage.load_original(req.file_id)
    if original is None:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    out = pipeline.load_analysis(req.file_id) or pipeline.analyze_original(req.file_id, original)
    if out.get("detect_failed"):
        raise HTTPException(status_code=503, detail="분석하지 못했습니다 — 변환은 할 수 있어요")
    reason = pipeline._composite_first_reason(out)
    route = "generate" if reason is None else "original" if reason == "inside_view" else "composite"
    objects = [{"index": i, **o} for i, o in enumerate(out.get("objects") or [])]
    return AnalyzeResponse(item=out.get("item", "object"), item_count=out.get("item_count") or 1,
                           objects=objects, photo_type=out.get("photo_type"),
                           wear_level=out.get("wear_level"), text_level=out.get("text_level"),
                           route=route, reason=reason)


@router.post("/transform", response_model=TransformResponse)
def transform(req: TransformRequest, background: BackgroundTasks):
    # def = 스레드풀 실행 (rembg 같은 블로킹 작업용 ✅)
    key = style_key(req.preset, req.note)
    if req.separate:
        key += f"-o{req.sell[0]}"     # 물건마다 따로 — 같은 사진의 다른 물건 결과를 덮어쓰지 않게 (10-05)
    try:
        out = pipeline.run_transform(req.file_id, key, defer_signals=True, note=req.note,
                                     composition=req.composition, sell=req.sell)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    except Exception:
        logger.exception("변환 실패")              # 서버 로그엔 상세히
        raise HTTPException(status_code=500, detail="변환 중 오류가 발생했습니다")

    # 관측 신호는 응답을 보낸 뒤 (사용자가 기다리지 않게) — 누끼 비교는 inspect 에.
    # judge 성적표는 운영에서 부르지 않는다 (10-05 — eval · dev 만)
    if out.get("item_signals_pending"):
        background.add_task(pipeline.item_signals_and_save, req.file_id, key,
                            trace_id=out.get("trace_id"),
                            parent_span_id=out.get("trace_span_id"))

    return TransformResponse(
        file_id=req.file_id,
        preset=key,
        result_path=f"storage/result/{out['result_name']}",
        result_url=storage.result_url(req.file_id, key),
        prompt_used=out["prompt_used"],
        gate_passed=out["gate_passed"],
        item=out["item"],                           # ⭐
        considered=out["considered"],
        mode=out.get("mode", "generate"),
        detect_failed=out.get("detect_failed", False),
        verify_failed=out.get("verify_failed", False),
        composite_reason=out.get("composite_reason"),
        photo_type=out.get("photo_type"),
        wear_level=out.get("wear_level"),
        watermark=out.get("watermark"),
    )

