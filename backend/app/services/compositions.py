"""종류별 정석 구도 — 사용자가 고르는 "결과 사진의 모양".

구도는 생성 모델이 만들지 않는다 (study 10-01 §4): 각 구도는 그 각도로 찍힌 사진이 있어야 고를 수 있고,
생성은 그 사진의 카메라 각도를 그대로 둔 채 그 구도의 틀(가운데 · 여백 · 나란히)로만 정리한다.
그래서 구도마다 "이 구도를 만들 수 있는 사진 각도(views)"를 함께 적는다.

프롬프트 문장은 각도를 바꾸라는 말을 하지 않는다 — 놓인 모양 · 여백 · 수평만. 종류별 문장은 그 종류일 때만
붙는다 (listing-clothes: 잠금에 종류별 예시를 다 넣자 신발 사진에 재킷을 그렸다).
그림은 frontend/img/compositions/ 의 선 그림 (남의 상품 사진을 쓰지 않는다).
"""
from app.services import coverage


def _ref(body: str) -> str:
    """구도 문장은 느슨한 참고로만 (10-03) — "80% 채워 · 정면으로 반듯하게 · 펼쳐서 대칭" 처럼 틀을 세게 주면
    사진이 거기 안 맞을 때 물건을 바꿔서 맞췄다 (CD 2장 → 1 · 3장, LP → CD 케이스). 각도 · 모양 · 개수는 그대로."""
    return (f"As a loose reference for the layout, think of {body}. Follow it only as far as this photo "
            "already allows: keep the item's angle, shape, size, number and packaging exactly as photographed, "
            "and add nothing that is not in the original photo.")

COMPOSITIONS: dict[str, list[dict]] = {
    "shoes": [
        {"key": "shoes_front34", "label": "대표컷 · 앞쪽 비스듬히",
         "desc": "한 켤레를 나란히, 앞코가 보이게 비스듬히 — 쇼핑몰 첫 사진",
         "views": {"front_34"},
         "prompt": _ref("a three-quarter footwear listing shot — the shoes side by side, front of the shoes toward the camera")},
        {"key": "shoes_side", "label": "옆모습",
         "desc": "바깥쪽 옆면 전체, 뒤꿈치부터 앞코까지 수평으로",
         "views": {"side"},
         "prompt": _ref("a side-profile footwear listing shot — the whole shoe from heel to toe, sole along the ground")},
        {"key": "shoes_top", "label": "위에서",
         "desc": "위에서 내려다본 한 켤레 — 끈 · 깔창 · 앞코 모양",
         "views": {"top"},
         "prompt": _ref("a top-down footwear listing shot — the shoes side by side seen from above")},
        {"key": "shoes_back", "label": "뒤꿈치",
         "desc": "뒤에서 본 뒤꿈치 — 힐컵 · 뒤축 닳음",
         "views": {"back", "rear_34"},
         "prompt": _ref("a heel-view footwear listing shot — the heels side by side")},
        {"key": "shoes_sole", "label": "밑창",
         "desc": "밑창 전체 — 닳은 정도가 보이게",
         "views": {"bottom"},
         "prompt": _ref("an outsole listing shot — the whole sole in view. Keep every worn area of the sole exactly as photographed")},
    ],
    # 10-03 예시 사진(eval/refs/)에서 뽑은 구도 — 상의는 종류(티셔츠 · 맨투맨 · 폴로 · 유니폼 · 재킷) 상관없이 하나
    "clothing": [
        {"key": "clothing_top_front", "label": "상의 앞판",
         "desc": "앞판 정면, 펼쳐서 좌우 대칭 — 옷걸이 · 손 없이",
         "views": {"front", "front_34"},
         "prompt": _ref("a flat front apparel listing shot — the front of the top facing the camera, no hanger, hands or mannequin")},
    ],
    # 10-05: 시계 · 책/게임/음반 · 세트를 other 에서 나눴다 (예전엔 other 물건이면 이 구도가 다 후보로 나왔다)
    "watch": [
        {"key": "watch_front34", "label": "시계 · 비스듬히",
         "desc": "다이얼이 보이게 비스듬히 세우고 줄은 뒤로 둥글게",
         "views": {"front", "front_34"},
         "prompt": _ref("a wristwatch listing shot — the dial toward the camera")},
    ],
    "media": [
        {"key": "book_cover34", "label": "책 · 표지 비스듬히",
         "desc": "앞표지와 책등이 함께 보이게 비스듬히 세운 한 권",
         "views": {"front", "front_34"},
         "prompt": _ref("a single-book listing shot — the front cover toward the camera")},
        {"key": "book_stack", "label": "책 · 쌓은 세트",
         "desc": "여러 권을 책등이 보이게 가지런히 쌓은 모습",
         "views": {"side", "front", "front_34"},
         "prompt": _ref("a book-set listing shot — the stack with its spines toward the camera")},
        {"key": "album_front", "label": "음반 · 앞면",
         "desc": "앞면을 정면으로 — 케이스 · 비닐 · 개수는 원본 그대로",
         "views": {"front", "front_34"},
         # 원본에 없을 수 있는 물건 이름(LP · CD · 케이스 · 비닐)을 쓰지 않는다 — "그대로"라고 해도 그걸 그린다
         # (10-03: LP→CD 케이스, CD→LP 슬리브, "비닐 그대로" → 없던 비닐)
         "prompt": _ref("a music album listing shot — the front cover toward the camera. Keep the packaging exactly as photographed")},
    ],
}
# 종류보다 좁은 물건 (coverage.SUBTYPES) — 있으면 종류의 구도 대신 이것만. 구도마다 "states" 가 있으면
# 각도와 상태가 둘 다 맞는 사진만 쓴다 (닫은 노트북 정면 사진으로 "펼쳐서 정면"을 고르지 않게).
# 각도 · 상태는 생성이 바꾸지 않는다 — 문장도 그 상태를 이미 찍은 사진에만 붙는다.
SUBTYPE_COMPOSITIONS: dict[str, list[dict]] = {
    "laptop": [
        {"key": "laptop_open34", "label": "노트북 · 펼쳐서 비스듬히",
         "desc": "펼친 노트북을 앞쪽에서 비스듬히 — 화면 · 키보드 · 옆면이 함께",
         "views": {"front_34"}, "states": {"open"},
         "prompt": _ref("a three-quarter laptop listing shot — screen and keyboard toward the camera")},
        {"key": "laptop_open_front", "label": "노트북 · 펼쳐서 정면",
         "desc": "펼친 노트북을 정면에서 — 화면과 키보드 전체",
         "views": {"front"}, "states": {"open"},
         "prompt": _ref("a straight-on laptop listing shot — screen and keyboard facing the camera")},
        {"key": "laptop_lid", "label": "노트북 · 닫은 상판",
         "desc": "닫은 노트북을 위에서 — 상판의 찍힘 · 흠집이 보이게",
         "views": {"top"}, "states": {"closed"},
         "prompt": _ref("a top-down laptop listing shot — the lid seen from above")},
    ],
}
BY_KEY = {c["key"]: c for cs in [*COMPOSITIONS.values(), *SUBTYPE_COMPOSITIONS.values()] for c in cs}


def _hint(c: dict) -> str:
    views = " · ".join(coverage.VIEWS[v] for v in sorted(c["views"]))
    if c.get("states"):
        sub = next(s for s, cs in SUBTYPE_COMPOSITIONS.items() if c in cs)
        views = f"{' · '.join(coverage.STATES[sub][s] for s in sorted(c['states']))} 상태로 {views}"
    return f"{views} 사진이 없어요 — 이 각도로 찍어 올려 주세요"


def options(category: str, photos: list[dict], subtype: str | None = None) -> list[dict]:
    """종류(또는 subtype)의 정석 구도 목록 + 구도마다 쓸 수 있는 사진(file_id, 다시 찍을 필요 없는 것 먼저).
    쓸 수 있는 사진이 없으면 photo_ids 가 비고 hint 로 무엇을 찍으면 되는지."""
    out = []
    table = SUBTYPE_COMPOSITIONS.get(subtype or "") or COMPOSITIONS.get(coverage.norm_category(category), [])
    for c in table:
        match = [p for p in photos if coverage.matches(p, c["views"], c.get("states"))]
        good = [p for p in match if not coverage.problems(p)]
        ids = [p["file_id"] for p in good + [p for p in match if p not in good]]
        out.append({"key": c["key"], "label": c["label"], "desc": c["desc"],
                    "image": f"/img/compositions/{c['key']}.svg",
                    "photo_ids": ids, "available": bool(ids),
                    "hint": None if ids else _hint(c)})
    return out


def prompt_for(key: str | None) -> str:
    """고른 구도 → 생성 프롬프트에 덧붙일 문장 (없거나 모르는 키면 빈 문자열)."""
    c = BY_KEY.get(key or "")
    return "\n\n" + c["prompt"] if c else ""
