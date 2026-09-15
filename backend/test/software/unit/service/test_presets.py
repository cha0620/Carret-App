"""app.prompts.presets.get_preset - Langfuse fallback 경로에서 계약 유지 검증."""
import pytest

import app.core.tracing as tracing
from app.prompts.presets import PRESETS, SECONDHAND_LOCK, get_preset


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


@pytest.mark.parametrize("key", list(PRESETS.keys()))
def test_get_preset_returns_expected_keys_and_secondhand_lock(key):
    preset = get_preset(key)
    assert set(preset.keys()) == {"name", "prompt", "bg_color"}
    assert preset["name"] == PRESETS[key]["name"]
    assert preset["bg_color"] == PRESETS[key]["bg_color"]
    assert SECONDHAND_LOCK in preset["prompt"]


def test_get_preset_prompt_matches_fallback_verbatim_when_langfuse_disabled():
    for key in PRESETS:
        preset = get_preset(key)
        assert preset["prompt"] == PRESETS[key]["fallback_prompt"]


def test_get_preset_unknown_key_raises_keyerror():
    with pytest.raises(KeyError):
        get_preset("not_a_real_preset_key")
