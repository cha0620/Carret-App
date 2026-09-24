from app.prompts.presets import text_lock


def test_empty_texts_add_nothing():
    assert text_lock([]) == ""
    assert text_lock([{"text": "  "}]) == ""


def test_texts_are_quoted_with_rough_position():
    out = text_lock([
        {"text": "ROLEX", "x1": 400, "y1": 100, "x2": 600, "y2": 200},
        {"text": "29", "x1": 700, "y1": 400, "x2": 800, "y2": 500},
        {"text": "no box"},
    ])
    assert '"ROLEX" (top-center)' in out
    assert '"29" (middle-right)' in out
    assert '"no box"' in out
    assert "Do not retype" in out and out.startswith("\n\n")


def test_duplicate_same_place_is_listed_once_and_quotes_are_neutralized():
    t = {"text": 'say "hi"', "x1": 0, "y1": 0, "x2": 10, "y2": 10}
    out = text_lock([t, dict(t)])
    assert out.count("say 'hi'") == 1


def test_line_cap():
    out = text_lock([{"text": f"t{i}"} for i in range(100)])
    assert '"t29"' in out and '"t30"' not in out
