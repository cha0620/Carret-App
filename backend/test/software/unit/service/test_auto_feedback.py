"""app.services.ai.auto_feedback - `generate_feedback()` (VLM 호출 래퍼) 검증/트레이싱.

`judge.py`와 같은 패턴으로 공용 클라이언트 `get_client()`를 쓰기 때문에
`auto_feedback.get_client` 이름을 가짜 클라이언트 팩토리로 바꿔치기해서 실제 네트워크
없이 검증한다 (test_judge.py 컨벤션 그대로).
"""
import json

import pytest

import app.core.tracing as tracing
from app.services.ai.auto_feedback import generate_feedback


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


# ── generate_feedback(): 가짜 VLM 클라이언트 ────────────────
class _Resp:
    def __init__(self, text, usage_metadata=None):
        self.text = text
        self.usage_metadata = usage_metadata


class FakeModels:
    def __init__(self, text, usage_metadata=None):
        self._text = text
        self._usage_metadata = usage_metadata
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        return _Resp(self._text, self._usage_metadata)


def _make_fake_get_client(text, usage_metadata=None):
    models = FakeModels(text, usage_metadata)

    class _Client:
        def __init__(self):
            self.models = models

    client = _Client()
    return (lambda: client), models


def test_generate_feedback_disabled_tracing_returns_validated_dict(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    raw = {"rating": 4, "comment": "괜찮아요"}
    fake_get_client, models = _make_fake_get_client(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "get_client", fake_get_client)

    out = generate_feedback(b"orig", b"result")

    assert out == {"rating": 4, "comment": "괜찮아요"}
    assert models.last_kwargs["model"] == auto_feedback_mod.settings.VLM_MODEL


def test_generate_feedback_clamps_rating_above_range(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    raw = {"rating": 7, "comment": "최고"}
    fake_get_client, _ = _make_fake_get_client(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "get_client", fake_get_client)

    out = generate_feedback(b"orig", b"result")

    assert out["rating"] == 5


def test_generate_feedback_clamps_rating_below_range(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    raw = {"rating": 0, "comment": "별로"}
    fake_get_client, _ = _make_fake_get_client(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "get_client", fake_get_client)

    out = generate_feedback(b"orig", b"result")

    assert out["rating"] == 1


def test_generate_feedback_blank_comment_normalized_to_none(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    raw = {"rating": 3, "comment": "   "}
    fake_get_client, _ = _make_fake_get_client(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "get_client", fake_get_client)

    out = generate_feedback(b"orig", b"result")

    assert out["comment"] is None


def test_generate_feedback_missing_comment_normalized_to_none(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    raw = {"rating": 3}
    fake_get_client, _ = _make_fake_get_client(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "get_client", fake_get_client)

    out = generate_feedback(b"orig", b"result")

    assert out["comment"] is None


def test_generate_feedback_enabled_tracing_invokes_obs_update_with_validated_output(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod

    class FakeObservation:
        def __init__(self):
            self.update_calls = []
        def update(self, **kw):
            self.update_calls.append(kw)

    class FakeObservationCM:
        def __init__(self, obs):
            self._obs = obs
        def __enter__(self):
            return self._obs
        def __exit__(self, *a):
            return False

    class FakeLangfuseClient:
        def __init__(self):
            self.calls = []
            self.obs = FakeObservation()
        def start_as_current_observation(self, **kw):
            self.calls.append(kw)
            return FakeObservationCM(self.obs)

    fake_lf = FakeLangfuseClient()
    monkeypatch.setattr(tracing, "_disabled", False, raising=False)
    monkeypatch.setattr(tracing, "_client", fake_lf, raising=False)

    raw = {"rating": 5, "comment": "좋아요"}
    fake_get_client, _ = _make_fake_get_client(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "get_client", fake_get_client)

    out = generate_feedback(b"orig", b"result")

    assert out == raw
    assert fake_lf.calls[0]["name"] == "auto_feedback"
    assert fake_lf.calls[0]["as_type"] == "generation"
    assert fake_lf.obs.update_calls == [{"output": raw, "usage_details": None}]


# ── 호출별 모델 · 이미지 Part (app.core.vlm) ──
from app.core.config import settings as _settings  # noqa: E402


def _res_level(part):
    r = part.media_resolution
    return None if r is None else str(getattr(r.level, "value", r.level))


def _raw():
    return json.dumps({"rating": 4, "comment": "괜찮아요"})


def test_generate_feedback_uses_auto_feedback_model_override(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    monkeypatch.setattr(_settings, "VLM_MODEL", "base-model")
    monkeypatch.setattr(_settings, "vlm_models", {"auto_feedback": " af-model "})
    fake_get_client, models = _make_fake_get_client(_raw())
    monkeypatch.setattr(auto_feedback_mod, "get_client", fake_get_client)
    generate_feedback(b"orig", b"result")
    assert models.last_kwargs["model"] == "af-model"


@pytest.mark.parametrize("models_setting", [{}, {"auto_feedback": ""}, {"judge": "x"}])
def test_generate_feedback_without_override_uses_base_model(monkeypatch, models_setting):
    import app.services.ai.auto_feedback as auto_feedback_mod
    monkeypatch.setattr(_settings, "VLM_MODEL", "base-model")
    monkeypatch.setattr(_settings, "vlm_models", models_setting)
    fake_get_client, models = _make_fake_get_client(_raw())
    monkeypatch.setattr(auto_feedback_mod, "get_client", fake_get_client)
    generate_feedback(b"orig", b"result")
    assert models.last_kwargs["model"] == "base-model"


def test_generate_feedback_passes_two_png_image_parts(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    monkeypatch.setattr(_settings, "vlm_media_resolution", {})
    fake_get_client, models = _make_fake_get_client(_raw())
    monkeypatch.setattr(auto_feedback_mod, "get_client", fake_get_client)
    generate_feedback(b"orig", b"result")
    a, b, prompt = models.last_kwargs["contents"]
    assert (a.inline_data.data, b.inline_data.data) == (b"orig", b"result")
    assert a.inline_data.mime_type == b.inline_data.mime_type == "image/png"
    assert _res_level(a) is None and _res_level(b) is None
    assert isinstance(prompt, str)


def test_generate_feedback_image_parts_follow_resolution_override(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    monkeypatch.setattr(_settings, "vlm_media_resolution", {"auto_feedback": "medium"})
    fake_get_client, models = _make_fake_get_client(_raw())
    monkeypatch.setattr(auto_feedback_mod, "get_client", fake_get_client)
    generate_feedback(b"orig", b"result")
    a, b, _ = models.last_kwargs["contents"]
    assert _res_level(a) == _res_level(b) == "MEDIA_RESOLUTION_MEDIUM"
