"""여러 장 사진의 각도 · 종류 값 정리와 다시 찍어야 할 사진.

VLM 은 사진마다 각도(VIEWS 중 하나)와 문제(가림·흐림)만 답한다. 종류는 VLM 이 CATEGORIES 중에서 고른다.
"""

VIEWS = {
    "front": "정면",
    "front_34": "앞쪽 비스듬히",
    "side": "옆면",
    "back": "뒷면",
    "rear_34": "뒤쪽 비스듬히",
    "top": "위에서",
    "bottom": "바닥 · 밑창",
    "inside": "안쪽",
    "label": "라벨 · 택",
    "detail": "가까이 (하자 · 디테일)",
}

# 10-09: 종류별 필수 면 · 예시 구도는 지웠다 — 빠진 사진은 AI 사진 검토(photo_review)가 알려 준다
CATEGORIES = ("shoes", "clothing", "bag", "electronics", "vehicle", "watch", "media", "pack", "other")

# 종류보다 좁은 물건 (10-04) — 모양이 바뀌는 물건은 각도만으로 어느 면이 보였는지 모른다 (펼친 노트북 정면 ≠
# 닫은 노트북 정면). 이런 물건만 상태(STATES)를 받고, 필요한 면을 (각도, 상태) 조합으로 적는다.
# 지금은 노트북 하나 — 종류(category)는 electronics 그대로 두고 subtype 으로만 갈린다.
SUBTYPES = {"laptop": "electronics"}             # subtype → 이 subtype 을 가질 수 있는 종류
STATES = {"laptop": {"open": "펼침", "closed": "닫음"}}


def norm_subtype(s, category: str | None) -> str | None:
    """VLM · 저장값의 subtype → 아는 값이고 종류가 맞을 때만 (노트북이 아닌 종류에 laptop 이 붙지 않게)."""
    s = str(s or "").strip().lower()
    return s if SUBTYPES.get(s) == norm_category(category) else None


def norm_state(v, subtype: str | None) -> str | None:
    v = str(v or "").strip().lower()
    return v if v in STATES.get(subtype or "", {}) else None


def norm_view(v) -> str | None:
    v = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
    return v if v in VIEWS else None


def norm_category(c) -> str:
    c = str(c or "").strip().lower()
    return c if c in CATEGORIES else "other"


CLOSE_UPS = {"label", "detail"}   # 원래 물건 일부만 찍는 사진 — "물건이 잘 안 보여요"를 적용하지 않는다


def problems(p: dict) -> list[str]:
    """다시 찍으면 좋은 이유들. 라벨 · 디테일 근접은 물건 전체가 안 보이는 게 정상이다 ."""
    out = []
    if not p.get("item_visible", True) and p.get("view") not in CLOSE_UPS:
        out.append("물건이 잘 안 보여요")
    if p.get("occluded"):
        out.append("손이나 다른 물건에 가려진 부분이 있어요")
    if p.get("blurry"):
        out.append("흐려요")
    return out

