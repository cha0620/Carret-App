import pytest

from app.core import vlm
from app.core.config import settings


def test_unlisted_call_uses_model_default(monkeypatch):
    monkeypatch.setattr(settings, "vlm_thinking", {})
    assert "some_new_call" not in vlm.DEFAULT_THINKING
    assert vlm.thinking("some_new_call") is None


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


# ── DEFAULT_THINKING: 폭주 상한 ──
def test_default_thinking_table_exact():
    assert vlm.DEFAULT_THINKING == {
        "verify": 2048, "item_text": 2048,
        "detect": 4096, "judge": 4096, "check_photo": 4096, "match": 4096,
        "classify": "minimal", "auto_feedback": "low",
    }


@pytest.mark.parametrize("name,budget", [
    ("verify", 2048), ("item_text", 2048),
    ("detect", 4096), ("judge", 4096), ("check_photo", 4096), ("match", 4096),
])
def test_default_budgets_are_caps(monkeypatch, name, budget):
    monkeypatch.setattr(settings, "vlm_thinking", {})
    cfg = vlm.thinking(name)
    assert cfg.thinking_budget == budget and cfg.thinking_level is None


def test_item_text_cap_can_be_overridden_or_disabled(monkeypatch):
    monkeypatch.setattr(settings, "vlm_thinking", {"item_text": 512})
    assert vlm.thinking("item_text").thinking_budget == 512
    monkeypatch.setattr(settings, "vlm_thinking", {"item_text": "default"})
    assert vlm.thinking("item_text") is None


# ── media_resolution / image_part ──
from google.genai import types  # noqa: E402


def _level(res):
    return None if res is None else str(getattr(res.level, "value", res.level))


def test_media_levels_and_defaults():
    assert vlm.MEDIA_LEVELS == {"low", "medium", "high"}
    assert vlm.DEFAULT_MEDIA_RESOLUTION == {"classify": "low", "check_photo": "low"}


@pytest.mark.parametrize("name,expected", [
    ("classify", "MEDIA_RESOLUTION_LOW"),
    ("check_photo", "MEDIA_RESOLUTION_LOW"),
    ("detect", None), ("verify", None), ("item_text", None), ("judge", None),
    ("some_new_call", None),
])
def test_media_resolution_defaults(monkeypatch, name, expected):
    monkeypatch.setattr(settings, "vlm_media_resolution", {})
    assert _level(vlm.media_resolution(name)) == expected


def test_media_resolution_returns_part_media_resolution(monkeypatch):
    monkeypatch.setattr(settings, "vlm_media_resolution", {})
    assert isinstance(vlm.media_resolution("classify"), types.PartMediaResolution)


@pytest.mark.parametrize("value,expected", [
    ("medium", "MEDIA_RESOLUTION_MEDIUM"),
    ("HIGH", "MEDIA_RESOLUTION_HIGH"),
    ("  Low ", "MEDIA_RESOLUTION_LOW"),
    ("default", None), (" DEFAULT ", None),
])
def test_media_resolution_override_case_and_whitespace(monkeypatch, value, expected):
    monkeypatch.setattr(settings, "vlm_media_resolution", {"detect": value})
    assert _level(vlm.media_resolution("detect")) == expected


def test_media_resolution_override_can_turn_default_off(monkeypatch):
    """classify 기본 low 를 설정의 "default" 로 끄면 모델 기본값(None)."""
    monkeypatch.setattr(settings, "vlm_media_resolution", {"classify": "default"})
    assert vlm.media_resolution("classify") is None
    assert _level(vlm.media_resolution("check_photo")) == "MEDIA_RESOLUTION_LOW"   # 나머지 기본 유지


@pytest.mark.parametrize("bad", ["ultra", "", "minimal", "media_resolution_low", 1, True])
def test_media_resolution_unknown_is_none_with_warning(monkeypatch, caplog, bad):
    monkeypatch.setattr(settings, "vlm_media_resolution", {"detect": bad})
    with caplog.at_level("WARNING", logger="carret.vlm"):
        assert vlm.media_resolution("detect") is None
    assert any("VLM_MEDIA_RESOLUTION[detect]" in r.getMessage() for r in caplog.records)


def test_media_resolution_default_or_unlisted_does_not_warn(monkeypatch, caplog):
    monkeypatch.setattr(settings, "vlm_media_resolution", {"detect": "default"})
    with caplog.at_level("WARNING", logger="carret.vlm"):
        vlm.media_resolution("detect")
        vlm.media_resolution("some_new_call")
    assert not [r for r in caplog.records if r.name == "carret.vlm"]


def test_image_part_sets_resolution_for_low_call(monkeypatch):
    monkeypatch.setattr(settings, "vlm_media_resolution", {})
    part = vlm.image_part(b"IMG", "image/jpeg", "classify")
    assert isinstance(part, types.Part)
    assert part.inline_data.data == b"IMG" and part.inline_data.mime_type == "image/jpeg"
    assert _level(part.media_resolution) == "MEDIA_RESOLUTION_LOW"


def test_image_part_leaves_resolution_unset_for_default_call(monkeypatch):
    monkeypatch.setattr(settings, "vlm_media_resolution", {})
    part = vlm.image_part(b"IMG", "image/png", "detect")
    assert part.media_resolution is None
    assert part.inline_data.mime_type == "image/png"


def test_image_part_follows_settings_override(monkeypatch):
    monkeypatch.setattr(settings, "vlm_media_resolution", {"judge": "medium"})
    assert _level(vlm.image_part(b"x", "image/png", "judge").media_resolution) \
        == "MEDIA_RESOLUTION_MEDIUM"


# ── model(name): 호출별 모델 ──
def test_default_models_table():
    # 2026-09-26 모델 비교로 정한 값 — 하자·글씨를 보는 호출은 기본 모델(VLM_MODEL) 유지
    assert vlm.DEFAULT_MODELS == {"classify": "gemini-3.5-flash-lite",
                                  "check_photo": "gemini-3.5-flash-lite"}


def test_model_falls_back_to_vlm_model(monkeypatch):
    monkeypatch.setattr(settings, "VLM_MODEL", "base-model")
    monkeypatch.setattr(settings, "vlm_models", {})
    assert vlm.model("detect") == "base-model"          # 기본 목록에 없는 호출
    assert vlm.model("classify") == "gemini-3.5-flash-lite"   # 기본 목록


def test_model_override_per_call_and_stripped(monkeypatch):
    monkeypatch.setattr(settings, "VLM_MODEL", "base-model")
    monkeypatch.setattr(settings, "vlm_models", {"classify": "  lite-model "})
    assert vlm.model("classify") == "lite-model"
    assert vlm.model("detect") == "base-model"


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


# ── config: 관대한 JSON 설정 (vlm_thinking / vlm_media_resolution / vlm_models) ──
@pytest.mark.parametrize("env,field", [
    ("VLM_THINKING", "vlm_thinking"),
    ("VLM_MEDIA_RESOLUTION", "vlm_media_resolution"),
    ("VLM_MODELS", "vlm_models"),
])
def test_broken_json_setting_is_empty_dict_with_field_warning(monkeypatch, caplog, env, field):
    from app.core.config import Settings
    monkeypatch.setenv(env, "{not json")
    with caplog.at_level("WARNING", logger="carret.config"):
        assert getattr(Settings(), field) == {}
    msgs = [r.getMessage() for r in caplog.records if r.name == "carret.config"]
    assert any(m.startswith(f"{env} JSON 파싱 실패") for m in msgs)


@pytest.mark.parametrize("env,field,raw,expected", [
    ("VLM_MEDIA_RESOLUTION", "vlm_media_resolution", '{"detect": "low"}', {"detect": "low"}),
    ("VLM_MODELS", "vlm_models", '{"classify": "lite"}', {"classify": "lite"}),
    ("VLM_MEDIA_RESOLUTION", "vlm_media_resolution", "", {}),
    ("VLM_MEDIA_RESOLUTION", "vlm_media_resolution", "   ", {}),
    ("VLM_MODELS", "vlm_models", '["not", "a", "dict"]', {}),
    ("VLM_MODELS", "vlm_models", '"str"', {}),
])
def test_json_settings_parse_or_fall_back(monkeypatch, env, field, raw, expected):
    from app.core.config import Settings
    monkeypatch.setenv(env, raw)
    assert getattr(Settings(), field) == expected


def test_json_settings_default_empty(monkeypatch):
    from app.core.config import Settings
    for env in ("VLM_MEDIA_RESOLUTION", "VLM_MODELS"):
        monkeypatch.delenv(env, raising=False)
    s = Settings()
    assert s.vlm_media_resolution == {} and s.vlm_models == {}


def test_media_resolution_from_env_flows_to_media_resolution(monkeypatch):
    from app.core.config import Settings
    monkeypatch.setenv("VLM_MEDIA_RESOLUTION", '{"detect": "Medium"}')
    monkeypatch.setattr(settings, "vlm_media_resolution", Settings().vlm_media_resolution)
    assert _level(vlm.media_resolution("detect")) == "MEDIA_RESOLUTION_MEDIUM"
