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


@pytest.mark.parametrize("category", ["bag", "vehicle", "electronics"])
def test_options_empty_for_other_kinds(category):
    assert C.options(category, [_p("a", "front")]) == []


def test_prompt_for():
    assert C.prompt_for("shoes_sole").startswith("\n\n") and "sole" in C.prompt_for("shoes_sole")
    assert C.prompt_for(None) == "" and C.prompt_for("nope") == ""


def test_new_categories_have_compositions():
    """10-03 예시 사진에서 뽑은 구도 — 상의는 하나, 시계 · 책 · CD 는 other 에."""
    assert [o["key"] for o in C.options("clothing", [_p("a", "front")])] == ["clothing_top_front"]
    keys = [o["key"] for o in C.options("other", [_p("a", "front")])]
    assert keys == ["watch_front34", "book_cover34", "book_stack", "album_front"]
    assert all(o["available"] for o in C.options("other", [_p("a", "front")]))
    assert C.options("", [_p("a", "front")]) == C.options("other", [_p("a", "front")])   # 모르는 종류 = other


def test_album_prompt_never_names_a_format():
    """형태 이름(LP · CD · record · vinyl)을 쓰면 모델이 형태를 다시 고른다 (10-03)."""
    low = C.BY_KEY["album_front"]["prompt"].lower()
    for bad in ("lp", "cd ", "record", "vinyl", "cassette"):
        assert bad not in low, bad
