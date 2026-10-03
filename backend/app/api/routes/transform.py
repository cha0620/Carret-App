"""변환 API - HTTP 껍데기.

하는 일 딱 3개:
1. 요청 검증 (스키마가 자동)
2. 파이프라인 호출
3. 에러 → 상태코드 번역
"""
import logging
import json


from fastapi import APIRouter, BackgroundTasks, HTTPException, Path

from app.prompts.presets import NOTE_MAX_CHARS, PRESETS, PURPOSES, mood_of, style_key
from app.schemas.image import (AnalyzeRequest, AnalyzeResponse, QualityReport, StylesResponse,
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
    try:
        out = pipeline.run_transform(req.file_id, key, defer_judge=True, note=req.note,
                                     composition=req.composition, sell=req.sell)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    except Exception:
        logger.exception("변환 실패")              # 서버 로그엔 상세히
        raise HTTPException(status_code=500, detail="변환 중 오류가 발생했습니다")

    # 관측 신호는 응답을 보낸 뒤 (사용자가 기다리지 않게) — 누끼 비교는 inspect 에, 성적표는 프론트가 폴링
    if out.get("item_signals_pending"):
        background.add_task(pipeline.item_signals_and_save, req.file_id, key,
                            trace_id=out.get("trace_id"),
                            parent_span_id=out.get("trace_span_id"))
    if out.get("judge_pending"):
        background.add_task(pipeline.judge_and_save, req.file_id, key,
                            trace_id=out.get("trace_id"),
                            parent_span_id=out.get("trace_span_id"))

    quality = None
    if not out.get("judge_pending"):   # 채점 대기 중이면 지금 있는 파일은 옛 것뿐 (삭제 실패 시)
        qbytes = storage.load("quality", f"{req.file_id}_{key}.json")
        if qbytes is not None:
            quality = QualityReport(**json.loads(qbytes.decode("utf-8")))
 
    return TransformResponse(
        file_id=req.file_id,
        preset=key,
        result_path=f"storage/result/{out['result_name']}",
        result_url=storage.result_url(req.file_id, key),
        prompt_used=out["prompt_used"],
        quality=quality,
        bubbles=out["bubbles"],
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
        judge_pending=out.get("judge_pending", False),
        
    )


@router.get("/quality/{file_id}/{preset}", response_model=QualityReport)
def quality(file_id: str = Path(pattern=r"^[a-f0-9]{32}$"),
            preset: str = Path(pattern=r"^[a-z_]+(-[0-9a-f]{6})?$")):
    """백그라운드 채점 결과 폴링용 — storage 를 거치므로 S3 모드에서도 보인다
    (/storage 정적 마운트는 로컬 디스크만 서빙)."""
    if mood_of(preset) not in PRESETS:
        raise HTTPException(status_code=422, detail="알 수 없는 무드")
    qbytes = storage.load("quality", f"{file_id}_{preset}.json")
    if qbytes is None:
        raise HTTPException(status_code=404, detail="채점 중이거나 성적표가 없습니다")
    return QualityReport(**json.loads(qbytes.decode("utf-8")))
