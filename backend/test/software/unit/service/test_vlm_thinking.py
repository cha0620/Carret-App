from app.core import vlm
from app.core.config import settings


def test_unlisted_call_uses_model_default(monkeypatch):
    monkeypatch.setattr(settings, "vlm_thinking", {})
    assert vlm.thinking("detect") is None


def test_level_string_maps_to_thinking_level(monkeypatch):
    monkeypatch.setattr(settings, "vlm_thinking", {"judge": "low"})
    cfg = vlm.thinking("judge")
    assert cfg.thinking_level.lower() == "low" and cfg.thinking_budget is None


def test_int_or_digit_string_maps_to_budget(monkeypatch):
    monkeypatch.setattr(settings, "vlm_thinking", {"verify": 512, "judge": "256"})
    assert vlm.thinking("verify").thinking_budget == 512
    assert vlm.thinking("judge").thinking_budget == 256


def test_env_override_wins_and_default_disables(monkeypatch):
    monkeypatch.setattr(settings, "vlm_thinking", {"verify": "default"})
    assert vlm.thinking("verify") is None


def test_defaults_cap_verify_and_disable_cheap_calls(monkeypatch):
    monkeypatch.setattr(settings, "vlm_thinking", {})
    assert vlm.thinking("verify").thinking_budget == 2048
    assert vlm.thinking("classify").thinking_level.lower() == "minimal"


def test_unknown_or_bool_values_fall_back_to_default(monkeypatch):
    monkeypatch.setattr(settings, "vlm_thinking", {"verify": "off", "judge": True, "classify": "LOW"})
    assert vlm.thinking("verify") is None
    assert vlm.thinking("judge") is None
    assert vlm.thinking("classify").thinking_level.lower() == "low"


def test_broken_env_json_does_not_crash_settings(monkeypatch):
    from app.core.config import Settings
    monkeypatch.setenv("VLM_THINKING", "{not json")
    assert Settings().vlm_thinking == {}
    monkeypatch.setenv("VLM_THINKING", '{"verify": 1024}')
    assert Settings().vlm_thinking == {"verify": 1024}
