"""services/compositions.py — 종류별 정석 구도, 구도별 사진 고르기, 생성 프롬프트 문장."""
from pathlib import Path

import pytest

from app.services import compositions as C

FRONTEND = Path(__file__).resolve().parents[5] / "frontend"


def _p(fid, view, **kw):
    return {"file_id": fid, "view": view, "occluded": False, "blurry": False, "item_visible": True, **kw}


def test_every_composition_has_image_file_and_known_views():
    from app.services.coverage import VIEWS
    for c in C.BY_KEY.values():
        assert (FRONTEND / "img" / "compositions" / f"{c['key']}.svg").is_file(), c["key"]
        assert c["views"] and c["views"] <= set(VIEWS)


def test_prompts_never_ask_to_change_the_angle():
    """구도 문장은 틀(가운데 · 여백 · 수평)만 — 각도를 바꾸라고 하면 안 보이던 면을 지어낸다 (study 10-01)."""
    for c in C.BY_KEY.values():
        low = c["prompt"].lower()
        for bad in ("rotate", "turn the", "from another", "three-quarter view of", "restage"):
            assert bad not in low, (c["key"], bad)


def test_options_order_good_photos_first_and_hint_when_missing():
    photos = [_p("a", "side", blurry=True), _p("b", "side"), _p("c", "rear_34"), _p("d", None)]
    opts = {o["key"]: o for o in C.options("shoes", photos)}
    assert opts["shoes_side"]["photo_ids"] == ["b", "a"]
    assert opts["shoes_back"]["photo_ids"] == ["c"]              # 뒤쪽 비스듬히도 뒤꿈치 구도로
    assert opts["shoes_front34"] == {**opts["shoes_front34"], "available": False, "photo_ids": []}
    assert "앞쪽 비스듬히" in opts["shoes_front34"]["hint"]


def test_album_prompt_never_names_a_format():
    """형태 이름(LP · CD · record · vinyl)을 쓰면 모델이 형태를 다시 고른다 (10-03)."""
    low = C.BY_KEY["album_front"]["prompt"].lower()
    for bad in ("lp", "cd ", "record", "vinyl", "cassette"):
        assert bad not in low, bad


# ══ 10-03: 구도 문장은 느슨한 참고 (_ref) — 틀을 세게 주면 물건을 바꿔서 맞췄다 ═══════
KEEP = "keep the item's angle, shape, size, number and packaging exactly as photographed"


@pytest.mark.parametrize("key", sorted(C.BY_KEY))
def test_every_prompt_has_no_hard_frame_orders(key):
    """퍼센트 · "square to the frame" · "filling" · "symmetric" 같은 센 틀 지시가 남아 있지 않다."""
    low = C.BY_KEY[key]["prompt"].lower()
    assert "%" not in low
    for bad in ("square to the frame", "filling about", "fill most", "symmetrically", "straight on",
                "even margins"):
        assert bad not in low, (key, bad)


def test_specific_notes_survive_ref_wrap():
    """sole 의 닳은 곳 · 앨범 포장 문장처럼 구도마다 붙인 당부는 _ref 안에 그대로 남는다."""
    assert "Keep every worn area of the sole exactly as photographed" in C.BY_KEY["shoes_sole"]["prompt"]
    assert "Keep the packaging exactly as photographed" in C.BY_KEY["album_front"]["prompt"]
    assert "no hanger, hands or mannequin" in C.BY_KEY["clothing_top_front"]["prompt"]
