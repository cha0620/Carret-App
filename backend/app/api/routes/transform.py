"""변환 API - HTTP 껍데기.

하는 일 딱 3개:
1. 요청 검증 (스키마가 자동)
2. 파이프라인 호출
3. 에러 → 상태코드 번역
"""
import logging
import json

from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Path

from app.schemas.image import TransformRequest, TransformResponse, QualityReport
from app.services import pipeline
from app.services.persistence import storage


logger = logging.getLogger("carret")
router = APIRouter()


@router.post("/transform", response_model=TransformResponse)
def transform(req: TransformRequest, background: BackgroundTasks):
    # def = 스레드풀 실행 (rembg 같은 블로킹 작업용 ✅)
    try:
        out = pipeline.run_transform(req.file_id, req.preset, defer_judge=True)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    except Exception:
        logger.exception("변환 실패")              # 서버 로그엔 상세히
        raise HTTPException(status_code=500, detail="변환 중 오류가 발생했습니다")

    # 성적표는 응답을 보낸 뒤 채점 (사용자가 judge 를 기다리지 않게) — 프론트가 폴링
    if out.get("judge_pending"):
        background.add_task(pipeline.judge_and_save, req.file_id, req.preset,
                            trace_id=out.get("trace_id"),
                            parent_span_id=out.get("trace_span_id"))

    quality = None
    if not out.get("judge_pending"):   # 채점 대기 중이면 지금 있는 파일은 옛 것뿐 (삭제 실패 시)
        qbytes = storage.load("quality", f"{req.file_id}_{req.preset}.json")
        if qbytes is not None:
            quality = QualityReport(**json.loads(qbytes.decode("utf-8")))
 
    return TransformResponse(
        file_id=req.file_id,
        preset=req.preset,
        result_path=f"storage/result/{out['result_name']}",
        result_url=storage.result_url(req.file_id, req.preset),
        prompt_used=out["prompt_used"],
        quality=quality,
        bubbles=out["bubbles"],
        gate_passed=out["gate_passed"],
        item=out["item"],                           # ⭐
        considered=out["considered"],
        mode=out.get("mode", "generate"),
        detect_failed=out.get("detect_failed", False),
        composite_reason=out.get("composite_reason"),
        status=out.get("status", "pass"),
        judge_pending=out.get("judge_pending", False),
        
    )


@router.get("/quality/{file_id}/{preset}", response_model=QualityReport)
def quality(file_id: str = Path(pattern=r"^[a-f0-9]{32}$"),
            preset: Literal["studio_white", "warm_wood", "minimal_gray"] = Path()):
    """백그라운드 채점 결과 폴링용 — storage 를 거치므로 S3 모드에서도 보인다
    (/storage 정적 마운트는 로컬 디스크만 서빙)."""
    qbytes = storage.load("quality", f"{file_id}_{preset}.json")
    if qbytes is None:
        raise HTTPException(status_code=404, detail="채점 중이거나 성적표가 없습니다")
    return QualityReport(**json.loads(qbytes.decode("utf-8")))
