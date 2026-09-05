from app.services.detector import _box, all_preserved, bubbles, match_anchors


def test_box_fixes_swapped_coords():                 # 회귀: 전치 사건
    b = _box({"x1": 600, "y1": 100, "x2": 300, "y2": 400})
    assert (b["x1"], b["y1"], b["x2"], b["y2"]) == (300, 100, 600, 400)


def test_bubbles_only_preserved_with_box():
    checks = [
        {"what": "a", "preserved": True,  "x1": 1, "y1": 1, "x2": 2, "y2": 2},
        {"what": "b", "preserved": False, "x1": 1, "y1": 1, "x2": 2, "y2": 2},
        {"what": "c", "preserved": True},
    ]
    assert [c["what"] for c in bubbles(checks)] == ["a"]


def test_all_preserved_empty_true():
    assert all_preserved([]) is True


def test_all_preserved_detects_false():
    assert all_preserved([{"preserved": True}, {"preserved": False}]) is False


def test_match_empty_result_all_missed():
    orig = [{"what": "얼룩", "where": "앞면"}]
    m = match_anchors(orig, [])
    assert m["missed"] == orig and m["new"] == [] and m["matched"] == []