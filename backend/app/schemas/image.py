"""요청/응답 스키마 = API의 계약서.

역할:
1. 들어오는 데이터 자동 검증 (틀리면 진입 전에 422)
2. /docs 문서 자동 생성
3. 1차 보안 게이트 (나쁜 입력은 함수까지 못 옴)
"""
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints, field_validator

class Bubble(BaseModel):
    what: str
    where: str = ""
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0

    
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
    gate_passed: bool | None = None


class TransformResponse(BaseModel):
    file_id: str
    preset: str
    result_path: str = Field(description="저장된 파일 경로")
    result_url: str = Field(description="브라우저에서 바로 보는 URL")
    prompt_used: str = Field(description="사용된 프롬프트 (실험 기록용)")
    gate_passed: bool | None = None
    item: str = "object"
    considered: list[str] = []
    composite_reason: str | None = Field(
        default=None, description="생성하지 않은 이유: detect_failed | inside_view | document | text_dense | "
                                  "text_heavy | wear_heavy | gate_failed | verify_failed")
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
    slot: Literal["product", "proof"] | None = Field(
        default=None, description="올린 칸 — 상품 사진 | 근거(보증서 · 정품 마크 …). 칸이 생기기 전 사진은 None")
    view: str | None = Field(default=None, description="각도 (coverage.VIEWS) — 모르면 None")
    view_label: str | None = None
    state: str | None = Field(default=None, description="놓인 모양 (coverage.STATES — 노트북 open | closed), 아니면 None")
    state_label: str | None = None
    occluded: bool = False
    blurry: bool = False
    item_visible: bool = True
    object: str | None = Field(default=None, description="이 사진이 어느 물건인지 (ItemObject.id) — 모르면 None")


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


class ItemObject(BaseModel):
    """사진 묶음 속 물건 하나 (10-04). AI 가 제안하고 사용자가 고친다."""
    id: str
    kind: Literal["product", "proof"] = Field(description="product 팔거나 보여 주는 물건 | proof 상품을 보증하는 사진")
    proof_type: str | None = Field(default=None, description="proof 만: document | mark | internals | screen | other")
    proof_type_label: str | None = None
    proof_for: str | None = Field(default=None, description="proof 만: 어느 상품을 보증하나 (id)")
    name: str = Field(description="짧은 영어 명사 (프롬프트용)")
    label: str = Field(description="사용자에게 보이는 이름")
    desc: str = ""
    category: str | None = Field(default=None, description="product 만: coverage.CATEGORIES")
    subtype: str | None = Field(default=None, description="종류보다 좁은 물건 (coverage.SUBTYPES — laptop), 아니면 None")
    for_sale: bool = False
    count: int = Field(default=1, description="같은 물건 몇 개 (product 만)")
    photo_ids: list[str] = Field(default_factory=list)
    missing: list[MissingView] = Field(default_factory=list)
    retake: list[Retake] = Field(default_factory=list)
    complete: bool = True
    compositions: list[CompositionOption] = Field(default_factory=list)


class ItemObjectEdit(BaseModel):
    id: str = Field(pattern=r"^o[0-9]{1,3}$")
    kind: Literal["product", "proof"]
    label: str = Field(default="", max_length=40)
    desc: str = Field(default="", max_length=200)
    category: str | None = Field(default=None, max_length=20)
    for_sale: bool = False
    count: int = Field(default=1, ge=1, le=99)
    proof_type: str | None = Field(default=None, max_length=20)
    proof_for: str | None = Field(default=None, pattern=r"^o[0-9]{1,3}$")


class ItemPhotoEdit(BaseModel):
    file_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    object: str | None = Field(default=None, pattern=r"^o[0-9]{1,3}$")
    view: str | None = Field(default=None, max_length=20)
    state: str | None = Field(default=None, max_length=20)


class ItemObjectsUpdate(BaseModel):
    """사용자가 고친 묶음 — 물건 목록 전체와 사진 전부의 물건 · 각도."""
    objects: list[ItemObjectEdit] = Field(max_length=20)
    photos: list[ItemPhotoEdit] = Field(max_length=60)


class ArrangeRequest(BaseModel):
    """파는 물건 여러 개를 한 장에 (10-04) — 물건마다 원본에서 오려 코드로 놓는다 (생성 없음)."""
    objects: list[Annotated[str, StringConstraints(pattern=r"^o[0-9]{1,3}$")]] = Field(
        min_length=2, max_length=6, description="놓을 물건 id (놓는 순서 — 왼쪽부터, 첫 물건이 겹칠 때 맨 위)")
    layout: Literal["row", "grid", "overlap"] = "row"
    photos: dict[Annotated[str, StringConstraints(pattern=r"^o[0-9]{1,3}$")],
                 Annotated[str, StringConstraints(pattern=r"^[a-f0-9]{32}$")]] = Field(
        default_factory=dict, max_length=6, description="물건 id → 쓸 사진 file_id (없으면 물건의 대표 사진)")


class ArrangeResponse(BaseModel):
    result_url: str
    photo_ids: list[str] = Field(description="물건마다 쓴 사진 (objects 순서)")


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
    objects: list[ItemObject] = Field(default_factory=list,
                                      description="사진 속 물건들 (10-04). 위의 item · category · missing · "
                                                  "compositions 는 대표 물건(파는 상품 중 사진이 가장 많은 것) 기준")
    main_object: str | None = Field(default=None, description="대표 물건 id")
    user_edited: bool = Field(default=False, description="사용자가 묶음을 확인 · 수정했나")
    needs_review: bool = Field(default=False, description="확인한 뒤 더 올린 사진을 AI 가 붙였다 — 다시 확인받는다")
