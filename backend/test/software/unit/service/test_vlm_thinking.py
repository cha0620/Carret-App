import pytest

from app.core import vlm
from app.core.config import settings


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


# ── media_resolution / image_part ──
from google.genai import types  # noqa: E402


def _level(res):
    return None if res is None else str(getattr(res.level, "value", res.level))


def test_media_resolution_override_can_turn_default_off(monkeypatch):
    """classify 기본 low 를 설정의 "default" 로 끄면 모델 기본값(None)."""
    monkeypatch.setattr(settings, "vlm_media_resolution", {"classify": "default"})
    assert vlm.media_resolution("classify") is None
    assert _level(vlm.media_resolution("check_photo")) == "MEDIA_RESOLUTION_LOW"   # 나머지 기본 유지


@pytest.mark.parametrize("bad", ["ultra", "",])
def test_media_resolution_unknown_is_none_with_warning(monkeypatch, caplog, bad):
    monkeypatch.setattr(settings, "vlm_media_resolution", {"detect": bad})
    with caplog.at_level("WARNING", logger="carret.vlm"):
        assert vlm.media_resolution("detect") is None
    assert any("VLM_MEDIA_RESOLUTION[detect]" in r.getMessage() for r in caplog.records)


def test_image_part_sets_resolution_for_low_call(monkeypatch):
    monkeypatch.setattr(settings, "vlm_media_resolution", {})
    part = vlm.image_part(b"IMG", "image/jpeg", "classify")
    assert isinstance(part, types.Part)
    assert part.inline_data.data == b"IMG" and part.inline_data.mime_type == "image/jpeg"
    assert _level(part.media_resolution) == "MEDIA_RESOLUTION_LOW"


@pytest.mark.parametrize("blank", ["", "   ", None])
def test_model_blank_override_falls_back(monkeypatch, blank):
    monkeypatch.setattr(settings, "VLM_MODEL", "base-model")
    monkeypatch.setattr(settings, "vlm_models", {"judge": blank})
    assert vlm.model("judge") == "base-model"


def test_model_settings_override_beats_default_models(monkeypatch):
    monkeypatch.setattr(vlm, "DEFAULT_MODELS", {"judge": "default-judge", "match": "default-match"})
    monkeypatch.setattr(settings, "VLM_MODEL", "base-model")
    monkeypatch.setattr(settings, "vlm_models", {"judge": "env-judge"})
    assert vlm.model("judge") == "env-judge"
    assert vlm.model("match") == "default-match"


