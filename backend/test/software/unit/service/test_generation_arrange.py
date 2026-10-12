"""판매자 요청(놓는 방법 · 구도, 10-12)을 생성 프롬프트에 붙이는 위치."""
from app.services.pipeline.generation import ARRANGE_NOTE, _with_arrange


def test_with_arrange_appends_at_end_without_retry_note():
    assert _with_arrange("Base.", "upright") == "Base." + ARRANGE_NOTE.format(arrange="upright")


def test_with_arrange_goes_before_retry_note():
    p = "Base.\n\nIMPORTANT: a previous attempt was cropped."
    out = _with_arrange(p, "upright")
    assert out == "Base." + ARRANGE_NOTE.format(arrange="upright") + "\n\nIMPORTANT: a previous attempt was cropped."
