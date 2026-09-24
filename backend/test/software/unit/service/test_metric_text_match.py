from app.services.quality.metric import text_match


def test_identical_lines_in_any_order_is_full_recall():
    m = text_match(["ROLEX", "DATEJUST", "29"], ["29", "rolex", "DATEJUST"])
    assert m["recall"] == 1.0 and m["changed"] == [] and m["added"] == []


def test_garbled_line_is_reported_as_changed():
    m = text_match(["OYSTER PERPETUAL"], ["OYSTER CONTRIAL"])
    assert m["recall"] < 1.0
    assert m["changed"][0]["before"] == "oyster perpetual"
    assert m["changed"][0]["after"] == "oyster contrial"


def test_new_unrelated_line_is_added():
    m = text_match(["ROLEX"], ["ROLEX", "SWISS MADE"])
    assert m["added"] == ["swiss made"]


def test_missing_line_scores_zero():
    m = text_match(["ROLEX", "29"], ["ROLEX"])
    assert m["recall"] < 1.0
    assert any(c["before"] == "29" for c in m["changed"])


def test_empty_before_is_full_recall():
    assert text_match([], ["x"])["recall"] == 1.0
    assert text_match([], [])["added"] == []
