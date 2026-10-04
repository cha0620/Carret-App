"""여러 장 사진의 각도 정리 — 종류별로 꼭 있어야 할 면이 빠졌는지, 다시 찍어야 할 사진이 있는지.

VLM 은 사진마다 각도(VIEWS 중 하나)와 문제(가림·흐림)만 답한다. 어떤 면이 필요한지는 여기서 정한다
(코드로 정해야 같은 물건에 매번 같은 안내가 나온다). 종류는 VLM 이 CATEGORIES 중에서 고른다.

10-01: 한 장으로 구도를 바꿔 다시 그리게 하면 안 보이던 면을 지어낸다 (study 10-01 §1·§2).
그래서 원하는 각도는 직접 찍게 하고, 빠진 면을 업로드 단계에서 알려준다.
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

# 종류 → [(필요한 면, 그 면으로 쳐 주는 각도들, 안내)]. 대체 가능한 각도는 함께 적는다
# (신발 앞쪽 3/4 는 정면을, 차 앞쪽 3/4 는 옆면을 어느 정도 보여준다 — 하지만 옆면을 대신하진 않는다).
REQUIRED = {
    "shoes": [
        ("front_34", {"front_34", "front"}, "앞쪽에서 비스듬히 한 켤레 나란히"),
        ("side", {"side"}, "바깥쪽 옆면"),
        ("back", {"back", "rear_34"}, "뒤꿈치"),
        ("bottom", {"bottom"}, "밑창 (닳은 정도가 보이게)"),
    ],
    "clothing": [
        ("front", {"front", "front_34"}, "앞판 전체 (펼쳐서)"),
        ("back", {"back", "rear_34"}, "뒤판 전체"),
        ("label", {"label"}, "목 · 안쪽 라벨 (사이즈 · 소재)"),
    ],
    "bag": [
        ("front", {"front", "front_34"}, "앞면"),
        ("back", {"back", "rear_34"}, "뒷면"),
        ("bottom", {"bottom"}, "바닥 (모서리 닳음)"),
        ("inside", {"inside"}, "안쪽"),
    ],
    "electronics": [
        ("front", {"front", "front_34"}, "앞면 (화면 · 버튼)"),
        ("back", {"back", "rear_34"}, "뒷면"),
        ("side", {"side"}, "옆면 (단자 · 버튼)"),
    ],
    "vehicle": [
        ("front_34", {"front_34", "front"}, "앞쪽에서 비스듬히"),
        ("rear_34", {"rear_34", "back"}, "뒤쪽에서 비스듬히"),
        ("side", {"side"}, "옆면 전체"),
        ("inside", {"inside"}, "실내 · 계기판"),
    ],
    "other": [
        ("front", {"front", "front_34"}, "앞면"),
        ("back", {"back", "rear_34", "side"}, "뒷면이나 옆면"),
    ],
}
CATEGORIES = tuple(REQUIRED)

# 종류보다 좁은 물건 (10-04) — 모양이 바뀌는 물건은 각도만으로 어느 면이 보였는지 모른다 (펼친 노트북 정면 ≠
# 닫은 노트북 정면). 이런 물건만 상태(STATES)를 받고, 필요한 면을 (각도, 상태) 조합으로 적는다.
# 지금은 노트북 하나 — 종류(category)는 electronics 그대로 두고 subtype 으로만 갈린다.
SUBTYPES = {"laptop": "electronics"}             # subtype → 이 subtype 을 가질 수 있는 종류
STATES = {"laptop": {"open": "펼침", "closed": "닫음"}}
# subtype → [(필요한 면 키, 이름, 안내, [(그 면으로 쳐 주는 각도들, 상태들 — None 이면 상태 상관없음)])]
REQUIRED_SUBTYPE = {
    "laptop": [
        ("open_front", "펼친 앞면", "펼쳐서 화면 · 키보드가 보이게", [({"front", "front_34"}, {"open"})]),
        ("lid", "상판", "닫은 상판 (찍힘 · 흠집)", [({"top"}, {"closed"}), ({"back", "rear_34"}, None)]),
        ("bottom", "하판", "뒤집어서 바닥", [({"bottom"}, None)]),
        ("side", "옆면 · 단자", "옆면 (단자)", [({"side"}, None)]),
    ],
}


def norm_subtype(s, category: str | None) -> str | None:
    """VLM · 저장값의 subtype → 아는 값이고 종류가 맞을 때만 (노트북이 아닌 종류에 laptop 이 붙지 않게)."""
    s = str(s or "").strip().lower()
    return s if SUBTYPES.get(s) == norm_category(category) else None


def norm_state(v, subtype: str | None) -> str | None:
    v = str(v or "").strip().lower()
    return v if v in STATES.get(subtype or "", {}) else None


def matches(p: dict, views: set, states: set | None) -> bool:
    """사진이 (각도, 상태) 조건에 맞나 — states 가 None 이면 각도만 본다."""
    return p.get("view") in views and (states is None or p.get("state") in states)


def norm_view(v) -> str | None:
    v = str(v or "").strip().lower().replace("-", "_").replace(" ", "_")
    return v if v in VIEWS else None


def norm_category(c) -> str:
    c = str(c or "").strip().lower()
    return c if c in REQUIRED else "other"


CLOSE_UPS = {"label", "detail"}   # 원래 물건 일부만 찍는 사진 — "물건이 잘 안 보여요"를 적용하지 않는다


def problems(p: dict) -> list[str]:
    """다시 찍으면 좋은 이유들. 라벨 · 디테일 근접은 물건 전체가 안 보이는 게 정상이다 (10-01 리뷰:
    안 빼면 옷의 필수 면 label 이 영원히 안 채워진다)."""
    out = []
    if not p.get("item_visible", True) and p.get("view") not in CLOSE_UPS:
        out.append("물건이 잘 안 보여요")
    if p.get("occluded"):
        out.append("손이나 다른 물건에 가려진 부분이 있어요")
    if p.get("blurry"):
        out.append("흐려요")
    return out


def check(category: str, photos: list[dict], subtype: str | None = None) -> dict:
    """photos: [{"view", "state", "occluded", "blurry", "item_visible"}] (VLM 답을 정리한 것).

    반환: {"missing": [{"view", "label", "hint"}], "retake": [{"index", "reason"}], "complete": bool}
    가려졌거나 흐리거나 물건이 안 보이는 사진은 그 각도를 채운 것으로 치지 않는다.
    subtype 이 있으면 그 물건의 (각도, 상태) 목록으로 본다 — missing 의 view 는 그때 면 키 (open_front 등)."""
    category = norm_category(category)
    good = [p for p in photos if p.get("view") and not problems(p)]
    if subtype in REQUIRED_SUBTYPE:
        missing = [{"view": key, "label": label, "hint": hint}
                   for key, label, hint, rules in REQUIRED_SUBTYPE[subtype]
                   if not any(matches(p, views, states) for p in good for views, states in rules)]
    else:
        seen = {p["view"] for p in good}
        missing = [{"view": view, "label": VIEWS[view], "hint": hint}
                   for view, ok_views, hint in REQUIRED[category] if not (seen & ok_views)]
    retake = [{"index": i, "reason": " · ".join(problems(p))} for i, p in enumerate(photos) if problems(p)]
    return {"missing": missing, "retake": retake, "complete": not missing and not retake}
