"""요청/응답 스키마 = API의 계약서.

역할:
1. 들어오는 데이터 자동 검증 (틀리면 진입 전에 422)
2. /docs 문서 자동 생성
3. 1차 보안 게이트 (나쁜 입력은 함수까지 못 옴)
"""
from typing import Literal

from pydantic import BaseModel, Field

class QualityReport(BaseModel):
    fidelity: int = 0
    realism: int = 0
    trust: int = 0
    analysis: str = ""

class TransformResponse(BaseModel):
    ...
    quality: QualityReport | None = None   # 🆕
# ===== 업로드 =====
class UploadResponse(BaseModel):
    file_id: str = Field(description="업로드 발급 uuid (변환의 열쇠)")
    filename: str = Field(description="사용자가 올린 원본 파일명")
    size_mb: float = Field(description="파일 크기(MB)")


# ===== 변환 =====
class TransformRequest(BaseModel):
    file_id: str = Field(
        pattern=r"^[a-f0-9]{32}$",   # ⭐ 우리가 발급한 uuid hex만 허용
        description="업로드 시 받은 file_id",
        examples=["a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6"],
    )
    preset: Literal["studio_white", "warm_wood", "minimal_gray"] = Field(
        default="studio_white",
        description="배경 스타일 프리셋",
    )


class TransformResponse(BaseModel):
    file_id: str
    preset: str
    result_path: str = Field(description="저장된 파일 경로")
    result_url: str = Field(description="브라우저에서 바로 보는 URL")
    prompt_used: str = Field(description="사용된 프롬프트 (실험 기록용)")
    quality: QualityReport | None = None