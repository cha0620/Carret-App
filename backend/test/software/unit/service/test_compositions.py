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


@pytest.mark.parametrize("category", ["clothing", "other", "", None, "SHOES?"])
def test_options_empty_for_other_kinds(category):
    assert C.options(category, [_p("a", "front")]) == []


def test_prompt_for():
    assert C.prompt_for("shoes_sole").startswith("\n\n") and "sole" in C.prompt_for("shoes_sole")
    assert C.prompt_for(None) == "" and C.prompt_for("nope") == ""
