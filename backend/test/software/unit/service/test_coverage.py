"""app.services.coverage — 각도 · 종류 값 정리와 다시 찍을 사진 (순수 함수)."""
import pytest

from app.services.coverage import norm_category, norm_view, problems


def _p(view, **kw):
    return {"view": view, "occluded": False, "blurry": False, "item_visible": True, **kw}


# ── norm_view · norm_category ───────────────
@pytest.mark.parametrize("raw,expected", [
    ("front", "front"),
    ("FRONT", "front"),
    ("  Side ", "side"),])
def test_norm_view_normalizes(raw, expected):
    assert norm_view(raw) == expected


def test_norm_category_unknown_is_other():
    assert norm_category("Shoes") == "shoes" and norm_category("spaceship") == "other"


# ── problems: 다시 찍을 이유 ────────────────
def test_problems_missing_keys_default_to_good():
    """item_visible 이 없으면 보인다고 본다 · occluded/blurry 없으면 문제 없음."""
    assert problems({"view": "front"}) == []


@pytest.mark.parametrize("flag,reason", [
    ({"occluded": True}, "손이나 다른 물건에 가려진 부분이 있어요"),
    ({"blurry": True}, "흐려요"),
    ({"item_visible": False}, "물건이 잘 안 보여요"),
])
def test_problems_bad_photo(flag, reason):
    assert problems(_p("front", **flag)) == [reason]


@pytest.mark.parametrize("view", ["label", "detail"])
def test_problems_close_up_not_visible_is_ok(view):
    """라벨 · 디테일 근접은 물건 전체가 안 보이는 게 정상 — 다시 찍으라고 하지 않는다."""
    assert problems(_p(view, item_visible=False)) == []
