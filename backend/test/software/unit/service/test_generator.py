"""app.services.generator._generate_ai - fal.ai 호출 래퍼 트레이싱 투명성.

`_generate_ai` 는 `fal_client.upload_file/subscribe` 와 `httpx.get` 을 쓰므로
generator 모듈의 `fal_client`/`httpx` 이름을 가짜 네임스페이스로 바꿔치기해서
실제 네트워크 없이 검증한다(비즈니스 로직 변경 없음 확인 목적).
"""
import pytest

import app.core.tracing as tracing
import app.services.generator as generator_mod
from app.services.generator import _generate_ai


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


class FakeFalClient:
    def __init__(self, out_url="https://fal.example/out.png"):
        self.out_url = out_url
        self.uploaded_paths = []
        self.subscribe_calls = []

    def upload_file(self, path):
        self.uploaded_paths.append(path)
        return "https://fal.example/uploaded.png"

    def subscribe(self, model, arguments):
        self.subscribe_calls.append({"model": model, "arguments": arguments})
        return {"images": [{"url": self.out_url}]}


class FakeHttpxResponse:
    def __init__(self, content):
        self.content = content


class FakeHttpx:
    def __init__(self, content=b"RESULT-BYTES"):
        self.content = content
        self.get_calls = []

    def get(self, url, timeout=None):
        self.get_calls.append({"url": url, "timeout": timeout})
        return FakeHttpxResponse(self.content)


def _preset():
    return {"prompt": "clean white background"}


def test_generate_ai_disabled_tracing_returns_downloaded_bytes_unchanged(monkeypatch):
    fake_fal = FakeFalClient()
    fake_httpx = FakeHttpx(content=b"IMG-BYTES")
    monkeypatch.setattr(generator_mod, "fal_client", fake_fal)
    monkeypatch.setattr(generator_mod, "httpx", fake_httpx)

    out = _generate_ai(b"original-bytes", _preset())

    assert out == b"IMG-BYTES"
    assert fake_fal.subscribe_calls[0]["arguments"]["prompt"] == "clean white background"
    assert fake_fal.subscribe_calls[0]["arguments"]["image_urls"] == [
        "https://fal.example/uploaded.png"
    ]
    assert fake_httpx.get_calls[0]["url"] == fake_fal.out_url
    assert fake_httpx.get_calls[0]["timeout"] == 60


def test_generate_ai_enabled_tracing_invokes_obs_update_with_output_url(monkeypatch):
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

    fake_fal = FakeFalClient(out_url="https://fal.example/final.png")
    fake_httpx = FakeHttpx(content=b"BYTES")
    monkeypatch.setattr(generator_mod, "fal_client", fake_fal)
    monkeypatch.setattr(generator_mod, "httpx", fake_httpx)

    out = _generate_ai(b"original-bytes", _preset())

    assert out == b"BYTES"
    assert fake_lf.calls[0]["name"] == "generate_image"
    assert fake_lf.calls[0]["as_type"] == "generation"
    assert fake_lf.obs.update_calls == [{"output": {"url": "https://fal.example/final.png"}}]
