"""app.services.ai.judge - `_system_prompt` (Langfuse fallback) + `judge()` (VLM 호출
래퍼) 트레이싱 투명성.

`judge()` 는 공용 클라이언트 `get_client()` 를 쓰기 때문에 `judge.get_client` 이름을
가짜 클라이언트 팩토리로 바꿔치기해서 실제 네트워크 없이 검증한다(비즈니스 로직
변경 없음 확인 목적 - 검증/스코어 클램프 로직은 건드리지 않는다).
"""
import json

import pytest

import app.core.tracing as tracing
from app.prompts.rubric import rubric_text
from app.services.ai.judge import _system_prompt, judge


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


def test_system_prompt_contains_live_rubric_text():
    text = _system_prompt()
    assert rubric_text() in text


def test_system_prompt_contains_unmangled_json_schema_example():
    """회귀: {{rubric}} 만 치환되고 단일 중괄호 JSON 예시는 그대로 남아야 한다
    (naive .format() 이었다면 KeyError 로 크래시하거나 예시가 깨졌을 것)."""
    text = _system_prompt()
    assert '{"analysis": "...", "fidelity": 1-5, "realism": 1-5, "trust": 1-5}' in text
    assert "{{rubric}}" not in text


def test_system_prompt_mentions_korean_analysis_instruction():
    text = _system_prompt()
    assert "KOREAN" in text
    assert "STRICT QC inspector" in text


# ── judge(): 가짜 VLM 클라이언트 ────────────────
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


def test_judge_disabled_tracing_returns_validated_report_unchanged(monkeypatch):
    import app.services.ai.judge as judge_mod
    raw = {"analysis": "괜찮음", "fidelity": 4, "realism": 6, "trust": 0}
    fake_get_client, models = _make_fake_get_client(json.dumps(raw))
    monkeypatch.setattr(judge_mod, "get_client", fake_get_client)

    report = judge(b"orig", b"result")

    # 클램프(1~5) 로직은 그대로 살아있어야 한다 - 트레이싱은 로직에 개입 안 함
    assert report == {"analysis": "괜찮음", "fidelity": 4, "realism": 5, "trust": 1}
    assert models.last_kwargs["model"] == judge_mod.settings.VLM_MODEL


def test_judge_enabled_tracing_invokes_obs_update_with_validated_output(monkeypatch):
    import app.services.ai.judge as judge_mod

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
    # 활성화: 직접 state 를 세팅해서 prompt_registry 쪽 get_langfuse() 호출도
    # 같은 fake 를 타게 만든다(네트워크 없음 - FakeLangfuseClient 에는 get_prompt
    # 가 없어서 prompt_registry 는 예외를 잡고 로컬 fallback 으로 자연히 떨어짐).
    monkeypatch.setattr(tracing, "_disabled", False, raising=False)
    monkeypatch.setattr(tracing, "_client", fake_lf, raising=False)

    raw = {"analysis": "굿", "fidelity": 3, "realism": 3, "trust": 3}
    fake_get_client, _ = _make_fake_get_client(json.dumps(raw))
    monkeypatch.setattr(judge_mod, "get_client", fake_get_client)

    report = judge(b"orig", b"result")

    assert report == raw
    assert fake_lf.calls[0]["name"] == "judge"
    assert fake_lf.calls[0]["as_type"] == "generation"
    assert fake_lf.obs.update_calls == [{"output": raw, "usage_details": None}]
