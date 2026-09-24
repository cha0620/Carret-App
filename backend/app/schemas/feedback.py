"""피드백 요청/응답 스키마."""
from pydantic import BaseModel, Field


class FeedbackRequest(BaseModel):
    file_id: str = Field(
        pattern=r"^[a-f0-9]{32}$",
        description="업로드 시 받은 file_id",
    )
    preset_key: str = Field(description="변환에 사용된 preset 값")
    rating: int = Field(ge=1, le=5, description="1~5점 평점")
    comment: str | None = Field(default=None, max_length=2000, description="자유 코멘트")
    tags: list[str] = Field(default_factory=list, max_length=10,
                            description="문제 유형 태그 (store.FEEDBACK_TAGS 밖의 값은 버림)")


class FeedbackResponse(BaseModel):
    file_id: str
    preset_key: str
    rating: int
    comment: str | None = None
    source: str = "user"
    tags: list[str] = []
    created_at: str | None = None
    updated_at: str | None = None
