"""피드백 API - HTTP 껍데기."""
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Path
from pydantic import BaseModel, Field

from app.schemas.feedback import FeedbackRequest, FeedbackResponse
from app.services.persistence import storage, store

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


# ── 최종본 확정 (10-11 사용자: "최종본은 사용자가 확정지으면 그걸로") ──
class FinalRequest(BaseModel):
    file_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    preset_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,120}$", description="확정할 결과 키 (transform · inpaint 의 preset)")
    source: Literal["app", "inpaint"] = "app"


@router.post("/final")
def confirm_final(req: FinalRequest):
    if not storage.exists("result", f"{req.file_id}_{req.preset_key}.jpg"):
        raise HTTPException(status_code=404, detail="확정할 사진을 찾을 수 없어요")
    store.confirm_final(req.file_id, req.preset_key, req.source)
    return store.get_final(req.file_id)


@router.get("/final/{file_id}")
def read_final(file_id: str = Path(pattern=r"^[a-f0-9]{32}$")):
    return store.get_final(file_id)
