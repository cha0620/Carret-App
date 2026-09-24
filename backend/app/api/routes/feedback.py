"""피드백 API - HTTP 껍데기."""
import logging

from fastapi import APIRouter, HTTPException, Path

from app.schemas.feedback import FeedbackRequest, FeedbackResponse
from app.services.persistence import store

logger = logging.getLogger("carret")
router = APIRouter()


@router.post("/feedback", response_model=FeedbackResponse)
def submit_feedback(req: FeedbackRequest):
    try:
        store.save_feedback(req.file_id, req.preset_key, req.rating, req.comment,
                            source="user", tags=req.tags)
    except Exception:
        logger.exception("피드백 저장 실패")
        raise HTTPException(status_code=500, detail="피드백 저장 중 오류가 발생했습니다")

    return store.get_feedbacks(req.file_id, req.preset_key)["user"]


@router.get("/feedback/{file_id}/{preset_key}", response_model=FeedbackResponse)
def read_feedback(
    file_id: str = Path(pattern=r"^[a-f0-9]{32}$"),
    preset_key: str = Path(...),
):
    # 사람이 남긴 것만 — 메인 앱의 피드백 칸을 에이전트 코멘트로 채우지 않기 위해
    row = store.get_feedbacks(file_id, preset_key)["user"]
    if not row:
        raise HTTPException(status_code=404, detail="피드백을 찾을 수 없습니다")
    return row
