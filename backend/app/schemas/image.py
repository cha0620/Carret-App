"""요청/응답 스키마 = API의 계약서.

역할:
1. 들어오는 데이터 자동 검증 (틀리면 진입 전에 422)
2. /docs 문서 자동 생성
3. 1차 보안 게이트 (나쁜 입력은 함수까지 못 옴)
"""
from typing import Literal

from pydantic import BaseModel, field_validator, Field

class Bubble(BaseModel):
    what: str
    where: str = ""
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0

class QualityReport(BaseModel):
    fidelity: int = 0
    realism: int = 0
    trust: int = 0
    analysis: str = ""

    
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
    preset: str = Field(
        default="studio_white",
        description="무드 키 (GET /api/styles) — 배경·조명만 바뀐다",
    )
    note: str = Field(default="", max_length=200,
                      description="원하는 분위기 한 줄 (선택, 40자) — 배경에만. 물건을 바꾸는 요청은 422")
    purpose: Literal["secondhand", "feed", "shop"] | None = Field(
        default=None, description="어디에 올릴 사진인가 — 무드 추천용, 결과는 바꾸지 않는다")
    composition: str | None = Field(
        default=None, description="고른 정석 구도 (GET /api/items 의 compositions[].key) — 구도의 틀로만 정리, 각도는 그대로")

    sell: list[int] | None = Field(
        default=None, max_length=12,
        description="팔 물건 (POST /api/analyze 의 objects[].index, 여러 개 가능) — 없으면 분석 판단대로")

    @field_validator("sell")
    @classmethod
    def _sell_indices(cls, v: list[int] | None) -> list[int] | None:
        if v is not None and (not v or any(i < 0 or i >= 12 for i in v)):
            raise ValueError("팔 물건을 하나 이상, 0~11 번호로 골라 주세요")
        return v

    @field_validator("composition")
    @classmethod
    def _known_composition(cls, v: str | None) -> str | None:
        from app.services.compositions import BY_KEY
        if v is not None and v not in BY_KEY:
            raise ValueError(f"알 수 없는 구도: {v}")
        return v

    @field_validator("preset")
    @classmethod
    def _known_mood(cls, v: str) -> str:
        from app.prompts.presets import PRESETS
        if v not in PRESETS:
            raise ValueError(f"알 수 없는 무드: {v}")
        return v

    @field_validator("note")
    @classmethod
    def _clean_note(cls, v: str) -> str:
        from app.prompts.presets import clean_note
        return clean_note(v)
    quality: QualityReport | None = None   # 🆕
    bubbles: list[Bubble] = []
    gate_passed: bool | None = None


class TransformResponse(BaseModel):
    file_id: str
    preset: str
    result_path: str = Field(description="저장된 파일 경로")
    result_url: str = Field(description="브라우저에서 바로 보는 URL")
    prompt_used: str = Field(description="사용된 프롬프트 (실험 기록용)")
    quality: QualityReport | None = None
    bubbles: list[Bubble] = []        
    gate_passed: bool | None = None
    item: str = "object"
    considered: list[str] = []
    composite_reason: str | None = Field(
        default=None, description="생성하지 않은 이유: detect_failed | inside_view | document | text_dense | "
                                  "text_heavy | wear_heavy | gate_failed | verify_failed")
    judge_pending: bool = Field(default=False,
                                description="성적표를 응답 뒤에 채점 중 — GET /api/quality/{file_id}/{preset} 폴링")
    verify_failed: bool = Field(default=False,
                                description="결과 보존 검사(verify) 호출 실패 — 보존 여부를 검증하지 못함")
    detect_failed: bool = Field(default=False,
                                description="원본 분석(analyze) 실패 — 보존 여부를 검증하지 못함")
    photo_type: str | None = Field(default=None, description="analyze: document | inside_view | product")
    wear_level: str | None = Field(default=None, description="analyze: none | light | heavy")
    watermark: str | None = Field(default=None, description="analyze: none | background | on_item")
    mode: str = Field(default="generate",
                      description='"generate" | "composite"(원본 물건 픽셀 + 배경만 교체) | "composite_failed" '
                                  '| "original"(원본 그대로 — 물건 일부·내부 사진 등)')

# ===== 스타일 · 미리 분석 =====
class Mood(BaseModel):
    key: str
    name: str
    emoji: str
    color: str = Field(description="배경 교체 때 쓰는 단색 (#rrggbb) — 카드 견본")


class Purpose(BaseModel):
    key: str
    name: str
    moods: list[str]


class StylesResponse(BaseModel):
    moods: list[Mood]
    purposes: list[Purpose]
    note_max_chars: int


class AnalyzeRequest(BaseModel):
    file_id: str = Field(pattern=r"^[a-f0-9]{32}$")


class SellObject(BaseModel):
    index: int
    what: str
    box: dict = Field(description="x1 · y1 · x2 · y2 (0-1000)")
    for_sale: bool = Field(description="분석이 판매 물건으로 본 것 — 화면의 기본 체크")


class AnalyzeResponse(BaseModel):
    item: str = "object"
    item_count: int = 1
    objects: list[SellObject] = Field(default_factory=list, description="사진 속 물건 — 사용자가 팔 물건을 고른다")
    photo_type: str | None = None
    wear_level: str | None = None
    text_level: str | None = None
    route: Literal["generate", "composite", "original"] = Field(
        description="예상 경로 — 생성(무드대로 배경) | 배경 교체(무드 색 단색) | 원본 그대로")
    reason: str | None = Field(default=None, description="생성하지 않는 이유 (plan 과 같은 값)")


# ===== 여러 각도 업로드 (10-01) =====
class ItemPhoto(BaseModel):
    file_id: str
    url: str
    source: Literal["photo", "video"] = Field(description="올린 사진 | 동영상에서 뽑은 장면")
    view: str | None = Field(default=None, description="각도 (coverage.VIEWS) — 모르면 None")
    view_label: str | None = None
    occluded: bool = False
    blurry: bool = False
    item_visible: bool = True


class MissingView(BaseModel):
    view: str
    label: str
    hint: str = Field(description="무엇을 찍으면 되는지")


class Retake(BaseModel):
    file_id: str
    reason: str


class CompositionOption(BaseModel):
    key: str
    label: str
    desc: str
    image: str = Field(description="구도 그림 주소 (선 그림)")
    photo_ids: list[str] = Field(default_factory=list, description="이 구도로 만들 수 있는 사진 (좋은 것 먼저)")
    available: bool = False
    hint: str | None = Field(default=None, description="쓸 사진이 없을 때 — 무엇을 찍으면 되는지")


class ItemResponse(BaseModel):
    item_id: str
    item: str = "object"
    category: str = "other"
    photos: list[ItemPhoto]
    missing: list[MissingView] = Field(default_factory=list, description="빠진 면 — 더 찍으면 좋은 것")
    retake: list[Retake] = Field(default_factory=list, description="다시 찍으면 좋은 사진")
    complete: bool = False
    views_failed: bool = Field(default=False, description="각도 분류 실패 — 빠진 면을 모른다")
    compositions: list[CompositionOption] = Field(default_factory=list,
                                                  description="이 종류의 정석 구도 (지금은 신발만)")
