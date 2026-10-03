"""app.services.coverage — 종류별로 필요한 면이 빠졌는지, 다시 찍을 사진이 있는지 (순수 함수)."""
import pytest

from app.services.coverage import CATEGORIES, REQUIRED, VIEWS, check, norm_category, norm_view


def _p(view, **kw):
    return {"view": view, "occluded": False, "blurry": False, "item_visible": True, **kw}


# ── 표 자체의 일관성 ─────────────────────────
def test_required_views_are_all_known_views():
    for cat, needs in REQUIRED.items():
        for view, ok_views, hint in needs:
            assert view in VIEWS, (cat, view)
            assert ok_views <= set(VIEWS), (cat, ok_views)
            assert view in ok_views, f"{cat}: 필요한 면 {view} 가 자기 자신으로 채워지지 않음"
            assert hint


def test_categories_include_other():
    assert "other" in CATEGORIES and set(CATEGORIES) == set(REQUIRED)


# ── norm_view ───────────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("front", "front"),
    ("FRONT", "front"),
    ("  Side ", "side"),
    ("front-34", "front_34"),
    ("rear 34", "rear_34"),
    ("Front_34", "front_34"),
])
def test_norm_view_normalizes(raw, expected):
    assert norm_view(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   ", "diagonal", 34, "front34", ["front"]])
def test_norm_view_unknown_is_none(raw):
    assert norm_view(raw) is None


# ── norm_category ───────────────────────────
@pytest.mark.parametrize("raw,expected", [
    ("shoes", "shoes"), (" SHOES ", "shoes"), ("Vehicle", "vehicle"),
    ("toy", "other"), (None, "other"), ("", "other"), (3, "other"), ("shoe", "other"),
])
def test_norm_category(raw, expected):
    assert norm_category(raw) == expected


# ── check: 빠진 면 ──────────────────────────
def test_check_empty_photos_all_missing():
    out = check("shoes", [])
    assert [m["view"] for m in out["missing"]] == ["front_34", "side", "back", "bottom"]
    assert out["retake"] == [] and out["complete"] is False


def test_check_missing_entries_carry_label_and_hint():
    out = check("clothing", [])
    m = out["missing"][-1]
    assert m == {"view": "label", "label": VIEWS["label"], "hint": "목 · 안쪽 라벨 (사이즈 · 소재)"}


def test_check_shoes_complete():
    out = check("shoes", [_p("front_34"), _p("side"), _p("back"), _p("bottom")])
    assert out == {"missing": [], "retake": [], "complete": True}


def test_check_alternate_view_fills_need():
    """신발 정면은 앞쪽 3/4 를, 뒤쪽 3/4 는 뒤꿈치를 대신한다."""
    out = check("shoes", [_p("front"), _p("side"), _p("rear_34"), _p("bottom")])
    assert out["complete"] is True


def test_check_vehicle_front34_does_not_replace_side():
    out = check("vehicle", [_p("front_34"), _p("rear_34"), _p("inside")])
    assert [m["view"] for m in out["missing"]] == ["side"]


def test_check_vehicle_back_counts_as_rear34():
    out = check("vehicle", [_p("front"), _p("back"), _p("side"), _p("inside")])
    assert out["complete"] is True


def test_check_other_side_counts_as_back():
    out = check("other", [_p("front"), _p("side")])
    assert out["complete"] is True


def test_check_unknown_category_uses_other():
    assert check("toy", []) == check("other", [])
    assert check(None, [_p("front"), _p("back")])["complete"] is True


def test_check_extra_views_do_not_hurt():
    out = check("other", [_p("front"), _p("back"), _p("detail"), _p("label"), _p("top")])
    assert out["complete"] is True


def test_check_view_none_fills_nothing_and_is_not_retake():
    out = check("other", [_p(None), _p(None)])
    assert len(out["missing"]) == 2 and out["retake"] == []


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


def test_check_bad_photo_view_still_filled_by_another_good_one():
    out = check("other", [_p("front", blurry=True), _p("front"), _p("back")])
    assert out["missing"] == []
    assert out["retake"] == [{"index": 0, "reason": "흐려요"}]
    assert out["complete"] is False             # 다시 찍을 사진이 있으면 완료 아님


def test_check_multiple_reasons_joined_in_fixed_order():
    out = check("other", [_p("front", blurry=True, occluded=True, item_visible=False)])
    assert out["retake"] == [{"index": 0, "reason":
                              "물건이 잘 안 보여요 · 손이나 다른 물건에 가려진 부분이 있어요 · 흐려요"}]


def test_check_retake_indices_point_to_original_positions():
    photos = [_p("front"), _p("back", occluded=True), _p(None), _p("side", blurry=True)]
    out = check("other", photos)
    assert [r["index"] for r in out["retake"]] == [1, 3]


def test_check_retake_for_photo_without_view():
    """각도를 몰라도 흐린 사진은 다시 찍으라고 한다."""
    out = check("other", [_p(None, blurry=True)])
    assert out["retake"] == [{"index": 0, "reason": "흐려요"}]


def test_check_does_not_mutate_input():
    photos = [_p("front", blurry=True)]
    snap = [dict(p) for p in photos]
    check("other", photos)
    assert photos == snap


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


@pytest.mark.parametrize("flag,reason", [
    ({"blurry": True}, "흐려요"),
    ({"occluded": True}, "손이나 다른 물건에 가려진 부분이 있어요"),
])
def test_check_close_up_still_retake_when_blurry_or_occluded(flag, reason):
    out = check("clothing", [_p("front"), _p("back"), _p("label", item_visible=False, **flag)])
    assert [m["view"] for m in out["missing"]] == ["label"]
    assert out["retake"] == [{"index": 2, "reason": reason}]


@pytest.mark.parametrize("view", ["front", "side", "inside", "top", None])
def test_check_non_close_up_not_visible_still_retake(view):
    out = check("other", [_p(view, item_visible=False)])
    assert out["retake"] == [{"index": 0, "reason": "물건이 잘 안 보여요"}]
