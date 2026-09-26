"""app.core.vlm.get_client — 프로세스 공용 Gemini 클라이언트 (lru_cache maxsize=1)
+ retryable(e) — VLM 호출 실패 중 다시 시도할 가치가 있는 것.

실제 네트워크 없이 `google.genai.Client` 생성자를 가짜로 바꿔 인자(api_key, 타임아웃 ms)
와 캐시(생성자 1회)만 확인한다. 캐시가 테스트 사이에 새지 않게 앞뒤로 cache_clear.
"""
import inspect
import json

import pytest
from google import genai
from google.genai import errors
from pydantic import ValidationError

from app.core import vlm
from app.core.config import Settings, settings


class _FakeClient:
    instances: list = []

    def __init__(self, **kw):
        self.kw = kw
        _FakeClient.instances.append(self)


@pytest.fixture(autouse=True)
def fake_genai_client(monkeypatch):
    vlm.get_client.cache_clear()
    _FakeClient.instances = []
    monkeypatch.setattr(genai, "Client", _FakeClient)
    yield
    vlm.get_client.cache_clear()


def _timeout_ms(client) -> int:
    return client.kw["http_options"].timeout


def test_get_client_passes_api_key_and_timeout_ms(monkeypatch):
    monkeypatch.setattr(settings, "VLM_KEY", "k-123")
    monkeypatch.setattr(settings, "vlm_timeout_s", 12.5)

    c = vlm.get_client()

    assert isinstance(c, _FakeClient)
    assert c.kw["api_key"] == "k-123"
    assert _timeout_ms(c) == 12500


def test_get_client_timeout_truncates_fractional_ms(monkeypatch):
    """int() 변환이라 1ms 미만 소수는 버려진다 (반올림 아님)."""
    monkeypatch.setattr(settings, "vlm_timeout_s", 1.0019)
    assert _timeout_ms(vlm.get_client()) == 1001


def test_get_client_is_cached_constructor_called_once():
    a = vlm.get_client()
    b = vlm.get_client()

    assert a is b
    assert len(_FakeClient.instances) == 1


def test_get_client_cache_ignores_later_settings_change(monkeypatch):
    """캐시된 뒤 설정을 바꿔도 같은 클라이언트 — 바꾸려면 cache_clear 가 필요."""
    monkeypatch.setattr(settings, "vlm_timeout_s", 5)
    first = vlm.get_client()
    monkeypatch.setattr(settings, "vlm_timeout_s", 30)
    assert vlm.get_client() is first and _timeout_ms(first) == 5000

    vlm.get_client.cache_clear()
    assert _timeout_ms(vlm.get_client()) == 30000
    assert len(_FakeClient.instances) == 2


def test_default_vlm_timeout_is_60s(monkeypatch):
    monkeypatch.delenv("VLM_TIMEOUT_S", raising=False)
    assert Settings.model_fields["vlm_timeout_s"].default == 60


@pytest.mark.parametrize("ok", ["1", "1.0"])
def test_vlm_timeout_lower_bound_inclusive(monkeypatch, ok):
    monkeypatch.setenv("VLM_TIMEOUT_S", ok)
    assert Settings().vlm_timeout_s == 1


@pytest.mark.parametrize("bad", [0, -1, 0.5, 0.999])
def test_vlm_timeout_must_be_positive(monkeypatch, bad):
    monkeypatch.setenv("VLM_TIMEOUT_S", str(bad))
    with pytest.raises(ValidationError):
        Settings()


def test_vlm_timeout_env_override(monkeypatch):
    monkeypatch.setenv("VLM_TIMEOUT_S", "2.5")
    assert Settings().vlm_timeout_s == 2.5


@pytest.mark.parametrize("mod_path", [
    "app.services.ai.detector", "app.services.ai.judge", "app.services.ai.auto_feedback",
])
def test_callers_import_shared_get_client_not_genai(mod_path):
    """호출부는 공용 클라이언트를 이름으로 가져온다 — 테스트는 `<module>.get_client`
    를 바꿔 끼운다. (유닛 conftest 가 실제 호출을 막으려 그 이름을 이미 덮어쓰므로
    객체 동일성 대신 소스로 확인.)"""
    import importlib
    mod = importlib.import_module(mod_path)
    src = inspect.getsource(mod)
    assert "from app.core.vlm import get_client" in src
    assert "genai.Client(" not in src
    assert not hasattr(mod, "genai")


# ── retryable(e) ────────────────────────────────
def _client_error(code):
    return errors.ClientError(code, {"error": {"code": code, "message": "m", "status": "S"}})


def _server_error(code):
    return errors.ServerError(code, {"error": {"code": code, "message": "m", "status": "S"}})


@pytest.mark.parametrize("err,expected", [
    (_client_error(429), True),             # 한도 초과 — 잠깐 쉬면 풀린다
    (_client_error(400), False),            # 요청 자체가 틀림 — 다시 해도 같다
    (_client_error(403), False),
    (_client_error(404), False),
    (_server_error(500), True),
    (_server_error(503), True),
    (TimeoutError("t/o"), True),
    (ValueError("strict: checks 없음"), True),   # 깨진 응답 — 다시 물으면 나을 수 있다
    (json.JSONDecodeError("x", "doc", 0), True),  # ValueError 하위
    (RuntimeError("down"), True),
    (ConnectionError("reset"), True),
    (TypeError("bad arg"), False),          # 코드 오류
    (AttributeError("x"), False),
    (KeyError("k"), False),
])
def test_retryable(err, expected):
    assert vlm.retryable(err) is expected


def test_retryable_client_error_without_code_is_not_retryable():
    e = _client_error(429)
    e.code = None
    assert vlm.retryable(e) is False


def test_retryable_subclasses_of_non_retryable_are_not_retryable():
    class MyKeyError(KeyError):
        pass
    assert vlm.retryable(MyKeyError("x")) is False
