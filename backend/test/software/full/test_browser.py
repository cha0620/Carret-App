import pytest

pytestmark = pytest.mark.e2e


def test_bubbles_render(page):
    """PIPELINE_MODE=mock 서버 기동 상태에서."""
    page.goto("http://127.0.0.1:8000")
    page.set_input_files("#file", "test/fixtures/stain.png")
    page.locator("#run").click()
    page.locator("#after").wait_for(state="visible", timeout=15000)
    assert page.locator(".bubble").count() >= 1