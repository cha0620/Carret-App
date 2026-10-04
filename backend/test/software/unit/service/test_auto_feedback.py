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


def test_generate_feedback_blank_comment_normalized_to_none(monkeypatch):
    import app.services.ai.auto_feedback as auto_feedback_mod
    raw = {"rating": 3, "comment": "   "}
    fake_get_client, _ = _make_fake_get_client(json.dumps(raw))
    monkeypatch.setattr(auto_feedback_mod, "get_client", fake_get_client)

    out = generate_feedback(b"orig", b"result")

    assert out["comment"] is None


# ── 호출별 모델 · 이미지 Part (app.core.vlm) ──
from app.core.config import settings as _settings  # noqa: E402


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


