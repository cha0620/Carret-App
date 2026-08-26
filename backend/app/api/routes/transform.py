"""변환 API - HTTP 껍데기.

하는 일 딱 3개:
1. 요청 검증 (스키마가 자동)
2. 파이프라인 호출
3. 에러 → 상태코드 번역
"""
import logging
import json

from fastapi import APIRouter, HTTPException

from app.schemas.image import TransformRequest, TransformResponse, QualityReport
from app.services import pipeline, storage


logger = logging.getLogger("carret")
router = APIRouter()


@router.post("/transform", response_model=TransformResponse)
def transform(req: TransformRequest):
    # def = 스레드풀 실행 (rembg 같은 블로킹 작업용 ✅)
    try:
        out = pipeline.run_transform(req.file_id, req.preset)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    except Exception:
        logger.exception("변환 실패")              # 서버 로그엔 상세히
        raise HTTPException(status_code=500, detail="변환 중 오류가 발생했습니다")

    quality = None
    qpath = storage.BASE / "quality" / f"{req.file_id}_{req.preset}.json"
    if qpath.exists():
        quality = QualityReport(**json.loads(qpath.read_text(encoding="utf-8")))
 
    return TransformResponse(
        file_id=req.file_id,
        preset=req.preset,
        result_path=f"storage/result/{out['result_name']}",
        result_url=storage.result_url(req.file_id, req.preset),
        prompt_used=out["prompt_used"],
        quality=quality,
        bubbles=out["bubbles"],
        gate_passed=out["gate_passed"]
        
    )

