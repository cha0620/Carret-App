"""app.services.coverage — 종류별로 필요한 면이 빠졌는지, 다시 찍을 사진이 있는지 (순수 함수)."""
import pytest

from app.services.coverage import check, norm_view


def _p(view, **kw):
    return {"view": view, "occluded": False, "blurry": False, "item_visible": True, **kw}


# ── norm_view ───────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("front", "front"),
    ("FRONT", "front"),
    ("  Side ", "side"),])
def test_norm_view_normalizes(raw, expected):
    assert norm_view(raw) == expected


# ── check: 빠진 면 ──────────────────────────
def test_check_empty_photos_all_missing():
    out = check("shoes", [])
    assert [m["view"] for m in out["missing"]] == ["front_34", "side", "back", "bottom"]
    assert out["retake"] == [] and out["complete"] is False


def test_check_alternate_view_fills_need():
    """신발 정면은 앞쪽 3/4 를, 뒤쪽 3/4 는 뒤꿈치를 대신한다."""
    out = check("shoes", [_p("front"), _p("side"), _p("rear_34"), _p("bottom")])
    assert out["complete"] is True


def test_check_missing_keys_default_to_good():
    """item_visible 이 없으면 보인다고 본다 · occluded/blurry 없으면 문제 없음."""
    out = check("other", [{"view": "front"}, {"view": "back"}])
    assert out["complete"] is True


# ── check: 문제 있는 사진 ───────────────────
@pytest.mark.parametrize("flag,reason", [
    ({"occluded": True}, "손이나 다른 물건에 가려진 부분이 있어요"),
    ({"blurry": True}, "흐려요"),
    ({"item_visible": False}, "물건이 잘 안 보여요"),
])
def test_check_bad_photo_does_not_fill_view_and_is_retake(flag, reason):
    out = check("other", [_p("front", **flag), _p("back")])
    assert [m["view"] for m in out["missing"]] == ["front"]
    assert out["retake"] == [{"index": 0, "reason": reason}]
    assert out["complete"] is False


def test_check_retake_indices_point_to_original_positions():
    photos = [_p("front"), _p("back", occluded=True), _p(None), _p("side", blurry=True)]
    out = check("other", photos)
    assert [r["index"] for r in out["retake"]] == [1, 3]


# ── 근접 사진 (label · detail) ──────────────
@pytest.mark.parametrize("view", ["label", "detail"])
def test_check_close_up_not_visible_is_not_retake(view):
    """라벨 · 디테일 근접은 물건 전체가 안 보이는 게 정상 — 다시 찍으라고 하지 않는다."""
    out = check("other", [_p("front"), _p("back"), _p(view, item_visible=False)])
    assert out["retake"] == [] and out["complete"] is True


def test_check_clothing_label_close_up_fills_label():
    """안 빼면 옷의 필수 면 label 이 영원히 안 채워진다 (10-01 리뷰)."""
    out = check("clothing", [_p("front"), _p("back"), _p("label", item_visible=False)])
    assert out == {"missing": [], "retake": [], "complete": True}


