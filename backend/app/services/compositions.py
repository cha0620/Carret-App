"""종류별 정석 구도 — 사용자가 고르는 "결과 사진의 모양".

구도는 생성 모델이 만들지 않는다 (study 10-01 §4): 각 구도는 그 각도로 찍힌 사진이 있어야 고를 수 있고,
생성은 그 사진의 카메라 각도를 그대로 둔 채 그 구도의 틀(가운데 · 여백 · 나란히)로만 정리한다.
그래서 구도마다 "이 구도를 만들 수 있는 사진 각도(views)"를 함께 적는다.

프롬프트 문장은 각도를 바꾸라는 말을 하지 않는다 — 놓인 모양 · 여백 · 수평만. 종류별 문장은 그 종류일 때만
붙는다 (listing-clothes: 잠금에 종류별 예시를 다 넣자 신발 사진에 재킷을 그렸다).
그림은 frontend/img/compositions/ 의 선 그림 (남의 상품 사진을 쓰지 않는다).
"""
from app.services import coverage

COMPOSITIONS: dict[str, list[dict]] = {
    "shoes": [
        {"key": "shoes_front34", "label": "대표컷 · 앞쪽 비스듬히",
         "desc": "한 켤레를 나란히, 앞코가 보이게 비스듬히 — 쇼핑몰 첫 사진",
         "views": {"front_34"},
         "prompt": "Frame it as a standard three-quarter footwear listing shot: the shoes side by side as "
                   "photographed, centered with even margins, filling about 80% of the frame, standing level "
                   "on the ground with a soft contact shadow."},
        {"key": "shoes_side", "label": "옆모습",
         "desc": "바깥쪽 옆면 전체, 뒤꿈치부터 앞코까지 수평으로",
         "views": {"side"},
         "prompt": "Frame it as a standard side-profile footwear shot: the shoe level with its sole flat on the "
                   "ground line, the whole shoe from heel to toe visible, centered with even margins."},
        {"key": "shoes_top", "label": "위에서",
         "desc": "위에서 내려다본 한 켤레 — 끈 · 깔창 · 앞코 모양",
         "views": {"top"},
         "prompt": "Frame it as a standard top-down footwear shot: the shoes side by side and parallel as "
                   "photographed, centered with even margins, filling about 80% of the frame."},
        {"key": "shoes_back", "label": "뒤꿈치",
         "desc": "뒤에서 본 뒤꿈치 — 힐컵 · 뒤축 닳음",
         "views": {"back", "rear_34"},
         "prompt": "Frame it as a standard heel-view footwear shot: the heels side by side as photographed, "
                   "centered with even margins, standing level on the ground."},
        {"key": "shoes_sole", "label": "밑창",
         "desc": "밑창 전체 — 닳은 정도가 보이게",
         "views": {"bottom"},
         "prompt": "Frame it as a standard outsole shot: the whole sole visible and centered with even margins. "
                   "Keep every worn area of the sole exactly as photographed."},
    ],
}
BY_KEY = {c["key"]: c for cs in COMPOSITIONS.values() for c in cs}


def options(category: str, photos: list[dict]) -> list[dict]:
    """종류의 정석 구도 목록 + 구도마다 쓸 수 있는 사진(file_id, 다시 찍을 필요 없는 것 먼저).
    쓸 수 있는 사진이 없으면 photo_ids 가 비고 hint 로 무엇을 찍으면 되는지."""
    out = []
    for c in COMPOSITIONS.get(coverage.norm_category(category), []):
        match = [p for p in photos if p.get("view") in c["views"]]
        good = [p for p in match if not coverage.problems(p)]
        ids = [p["file_id"] for p in good + [p for p in match if p not in good]]
        views = " · ".join(coverage.VIEWS[v] for v in sorted(c["views"]))
        out.append({"key": c["key"], "label": c["label"], "desc": c["desc"],
                    "image": f"/img/compositions/{c['key']}.svg",
                    "photo_ids": ids, "available": bool(ids),
                    "hint": None if ids else f"{views} 사진이 없어요 — 이 각도로 찍어 올려 주세요"})
    return out


def prompt_for(key: str | None) -> str:
    """고른 구도 → 생성 프롬프트에 덧붙일 문장 (없거나 모르는 키면 빈 문자열)."""
    c = BY_KEY.get(key or "")
    return "\n\n" + c["prompt"] if c else ""
