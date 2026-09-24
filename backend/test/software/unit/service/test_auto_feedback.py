"""app.services.ai.auto_feedback - `generate_feedback()` (VLM 호출 래퍼) 검증/트레이싱.

`judge.py`와 같은 패턴으로 `google.genai.Client`를 새로 만들기 때문에
`auto_feedback.genai` 이름을 가짜 네임스페이스로 바꿔치기해서 실제 네트워크
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


# ── generate_feedback(): 가짜 genai 클라이언트 ────────────────
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


def _make_fake_genai(text, usage_metadata=None):
    models = FakeModels(text, usage_metadata)

    class _Client:
        def __init__(self, **kw):
            self.models = models

    class _Genai:
        Client = _Client

    return _Genai(), models


def test_generate_feedback_disabled_tracing_returns_validated_dict(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    raw = {"rating": 4, "comment": "괜찮아요"}
    fake_genai, models = _make_fake_genai(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "genai", fake_genai)

    out = generate_feedback(b"orig", b"result")

    assert out == {"rating": 4, "comment": "괜찮아요"}
    assert models.last_kwargs["model"] == auto_feedback_mod.settings.VLM_MODEL


def test_generate_feedback_clamps_rating_above_range(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    raw = {"rating": 7, "comment": "최고"}
    fake_genai, _ = _make_fake_genai(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "genai", fake_genai)

    out = generate_feedback(b"orig", b"result")

    assert out["rating"] == 5


def test_generate_feedback_clamps_rating_below_range(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    raw = {"rating": 0, "comment": "별로"}
    fake_genai, _ = _make_fake_genai(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "genai", fake_genai)

    out = generate_feedback(b"orig", b"result")

    assert out["rating"] == 1


def test_generate_feedback_blank_comment_normalized_to_none(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    raw = {"rating": 3, "comment": "   "}
    fake_genai, _ = _make_fake_genai(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "genai", fake_genai)

    out = generate_feedback(b"orig", b"result")

    assert out["comment"] is None


def test_generate_feedback_missing_comment_normalized_to_none(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    raw = {"rating": 3}
    fake_genai, _ = _make_fake_genai(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "genai", fake_genai)

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
    fake_genai, _ = _make_fake_genai(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "genai", fake_genai)

    out = generate_feedback(b"orig", b"result")

    assert out == raw
    assert fake_lf.calls[0]["name"] == "auto_feedback"
    assert fake_lf.calls[0]["as_type"] == "generation"
    assert fake_lf.obs.update_calls == [{"output": raw, "usage_details": None}]
