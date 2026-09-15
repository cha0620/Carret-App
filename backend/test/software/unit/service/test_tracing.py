"""app.core.tracing - observe()/flush()/get_langfuse() 엣지 케이스.

핵심 계약:
  - Langfuse 비활성(get_langfuse() -> None)이면 observe() 는 순수 noop
    컨텍스트 매니저 — None 을 넘기고, 실제 클라이언트/네트워크에 절대 손대지 않는다.
  - Langfuse 활성이면 observe() 는 `lf.start_as_current_observation(name=...,
    as_type=..., **kw)` 를 그대로 호출하고 그 결과 컨텍스트가 넘겨주는 객체를
    그대로 yield 한다 (얇은 위임 — 로직 없음).
  - 본문에서 예외가 나면 그대로 전파된다(SDK 가 ERROR 로 마킹하는 건 SDK 책임,
    observe() 자체는 삼키지 않는다).
  - flush() 는 클라이언트 없으면 조용히 아무 일도 안 한다.

실제 Langfuse SDK/네트워크는 절대 사용하지 않는다 — 전부 작은 fake 로 대체.
"""
import pytest

import app.core.tracing as tracing


@pytest.fixture(autouse=True)
def reset_tracing_singleton(monkeypatch):
    """이전 테스트가 캐시해놓은 _client/_disabled 오염 방지 (기본값: 비활성)."""
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


class FakeObservation:
    """`.update(...)` 호출을 기록하는 가짜 observation 객체."""

    def __init__(self):
        self.update_calls = []

    def update(self, **kw):
        self.update_calls.append(kw)


class FakeObservationCM:
    """`with lf.start_as_current_observation(...) as obs:` 가 기대하는
    컨텍스트 매니저 모양만 흉내 낸다."""

    def __init__(self, obs, raise_on_exit=None):
        self._obs = obs
        self._raise_on_exit = raise_on_exit

    def __enter__(self):
        return self._obs

    def __exit__(self, exc_type, exc, tb):
        return False    # 예외를 삼키지 않고 그대로 전파


class FakeLangfuseClient:
    """`get_langfuse()` 가 돌려줄 법한 실제 Langfuse v4 클라이언트의 최소 흉내.
    네트워크는 전혀 없다 — 순수 파이썬 객체."""

    def __init__(self):
        self.calls = []
        self.obs = FakeObservation()
        self.flushed = False
        self.score_calls = []

    def start_as_current_observation(self, **kw):
        self.calls.append(kw)
        return FakeObservationCM(self.obs)

    def flush(self):
        self.flushed = True

    def score_current_trace(self, **kw):
        self.score_calls.append(kw)


# ── observe(): 비활성 경로 ───────────────────────
def test_observe_disabled_yields_none():
    with tracing.observe("anything") as obs:
        assert obs is None


def test_observe_disabled_yields_none_regardless_of_kwargs():
    with tracing.observe("detect", as_type="generation", model="m", input="p") as obs:
        assert obs is None


def test_observe_disabled_never_calls_get_langfuse_construction_path(monkeypatch):
    """비활성(_disabled=True)이면 get_langfuse() 는 `if _disabled: return None`
    에서 즉시 반환 — `from langfuse import Langfuse` 줄까지 내려가지 않는다.
    실제 SDK 생성자를 부수어 두고, 호출되면 바로 실패하게 만들어 검증한다."""
    from langfuse import Langfuse as RealLangfuse

    def _boom(*a, **kw):
        raise AssertionError("실제 Langfuse 생성 경로를 타면 안 된다")

    monkeypatch.setattr("langfuse.Langfuse", _boom)
    try:
        with tracing.observe("x", as_type="generation", model="m") as obs:
            assert obs is None
    finally:
        monkeypatch.setattr("langfuse.Langfuse", RealLangfuse)


def test_observe_disabled_body_exception_still_propagates():
    with pytest.raises(ValueError, match="boom"):
        with tracing.observe("x"):
            raise ValueError("boom")


# ── observe(): 활성 경로 (fake client) ────────────
def test_observe_enabled_delegates_to_start_as_current_observation(monkeypatch):
    fake = FakeLangfuseClient()
    monkeypatch.setattr(tracing, "get_langfuse", lambda: fake)

    with tracing.observe("detect", as_type="generation", model="gemini",
                          input="prompt") as obs:
        assert obs is fake.obs

    assert fake.calls == [{
        "name": "detect", "as_type": "generation",
        "model": "gemini", "input": "prompt",
    }]


def test_observe_enabled_default_as_type_is_span(monkeypatch):
    fake = FakeLangfuseClient()
    monkeypatch.setattr(tracing, "get_langfuse", lambda: fake)

    with tracing.observe("transform"):
        pass

    assert fake.calls[0]["as_type"] == "span"
    assert fake.calls[0]["name"] == "transform"


def test_observe_enabled_yields_whatever_the_fake_cm_yields(monkeypatch):
    sentinel = object()

    class OneOffClient:
        def start_as_current_observation(self, **kw):
            return FakeObservationCM(sentinel)

    monkeypatch.setattr(tracing, "get_langfuse", lambda: OneOffClient())

    with tracing.observe("x") as obs:
        assert obs is sentinel


def test_observe_enabled_body_exception_still_propagates(monkeypatch):
    fake = FakeLangfuseClient()
    monkeypatch.setattr(tracing, "get_langfuse", lambda: fake)

    with pytest.raises(RuntimeError, match="boom"):
        with tracing.observe("x", as_type="generation"):
            raise RuntimeError("boom")


def test_observe_enabled_obs_update_recorded_on_fake(monkeypatch):
    fake = FakeLangfuseClient()
    monkeypatch.setattr(tracing, "get_langfuse", lambda: fake)

    with tracing.observe("classify", as_type="generation") as obs:
        obs.update(output={"item": "chair"}, usage_details=None)

    assert fake.obs.update_calls == [{"output": {"item": "chair"}, "usage_details": None}]


# ── score() ──────────────────────────────────────
def test_score_disabled_is_noop_and_does_not_raise():
    tracing.score("visual_similarity", 0.9)  # 그냥 아무 일도 안 나야 함


def test_score_enabled_calls_score_current_trace_with_name_value_and_kwargs(monkeypatch):
    fake = FakeLangfuseClient()
    monkeypatch.setattr(tracing, "get_langfuse", lambda: fake)

    tracing.score("fidelity", 4, data_type="NUMERIC")

    assert fake.score_calls == [{"name": "fidelity", "value": 4, "data_type": "NUMERIC"}]


def test_score_enabled_with_none_value_still_forwards_as_is(monkeypatch):
    fake = FakeLangfuseClient()
    monkeypatch.setattr(tracing, "get_langfuse", lambda: fake)

    tracing.score("visual_similarity", None)

    assert fake.score_calls == [{"name": "visual_similarity", "value": None}]


# ── flush() ──────────────────────────────────────
def test_flush_noop_when_disabled_does_not_raise():
    tracing.flush()  # 그냥 아무 일도 안 나야 함


def test_flush_calls_client_flush_when_enabled(monkeypatch):
    fake = FakeLangfuseClient()
    monkeypatch.setattr(tracing, "get_langfuse", lambda: fake)

    tracing.flush()

    assert fake.flushed is True


# ── get_langfuse() ───────────────────────────────
def test_get_langfuse_returns_none_when_keys_empty(monkeypatch):
    monkeypatch.setattr(tracing, "_disabled", False, raising=False)
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    from app.core.config import settings
    monkeypatch.setattr(settings, "langfuse_public_key", "", raising=False)
    monkeypatch.setattr(settings, "langfuse_secret_key", "", raising=False)

    assert tracing.get_langfuse() is None
    assert tracing._disabled is True   # 이후 호출부터는 재확인 없이 바로 꺼짐


def test_get_langfuse_returns_cached_client_without_reconstructing(monkeypatch):
    """_client 가 이미 있으면 재구성(=네트워크/생성자) 없이 그대로 반환."""
    monkeypatch.setattr(tracing, "_disabled", False, raising=False)
    sentinel = object()
    monkeypatch.setattr(tracing, "_client", sentinel, raising=False)

    assert tracing.get_langfuse() is sentinel
