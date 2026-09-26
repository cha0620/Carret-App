"""app.services.ai.generator._generate_ai - fal.ai 호출 래퍼 트레이싱 투명성.

`_generate_ai` 는 `fal_client.upload_file/subscribe` 와 `httpx.get` 을 쓰므로
generator 모듈의 `fal_client`/`httpx` 이름을 가짜 네임스페이스로 바꿔치기해서
실제 네트워크 없이 검증한다(비즈니스 로직 변경 없음 확인 목적).
"""
import httpx
import pytest

import app.core.tracing as tracing
import app.services.ai.generator as generator_mod
from app.services.ai.generator import _generate_ai


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
        self.raise_calls = 0

    def raise_for_status(self):
        self.raise_calls += 1
        return self


class FakeHttpx:
    # _download 의 except 절이 모듈 이름으로 찾는다 — 진짜 예외 클래스를 그대로 노출
    HTTPStatusError = httpx.HTTPStatusError
    TransportError = httpx.TransportError

    def __init__(self, content=b"RESULT-BYTES"):
        self.content = content
        self.get_calls = []

    def get(self, url, timeout=None, follow_redirects=False):
        self.get_calls.append({"url": url, "timeout": timeout, "follow_redirects": follow_redirects})
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


def test_generate_ai_checks_status_before_returning(monkeypatch):
    fake_fal = FakeFalClient()
    responses = []
    fake_httpx = FakeHttpx(content=b"OK")
    real_get = fake_httpx.get
    fake_httpx.get = (lambda url, timeout=None, follow_redirects=False:
                      responses.append(real_get(url, timeout, follow_redirects)) or responses[-1])
    monkeypatch.setattr(generator_mod, "fal_client", fake_fal)
    monkeypatch.setattr(generator_mod, "httpx", fake_httpx)
    assert _generate_ai(b"o", _preset()) == b"OK"
    assert [r.raise_calls for r in responses] == [1]


# ══ _download: 상태 코드 확인 + 5xx·연결 오류 1회 재시도 ══
URL = "https://fal.example/out.png"


def _resp(status, content=b""):
    return httpx.Response(status, content=content, request=httpx.Request("GET", URL))


def _get_seq(monkeypatch, seq):
    """httpx.get 이 호출마다 seq 항목을: 예외면 raise, 아니면 응답 반환."""
    it = iter(seq)
    calls = []

    def fake(url, timeout=None, follow_redirects=False):
        calls.append({"url": url, "timeout": timeout, "follow_redirects": follow_redirects})
        v = next(it)
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(generator_mod.httpx, "get", fake)
    return calls


def test_download_attempts_constant():
    assert generator_mod.DOWNLOAD_ATTEMPTS == 2


def test_download_success_single_call(monkeypatch, capsys):
    calls = _get_seq(monkeypatch, [_resp(200, b"PNG")])
    assert generator_mod._download(URL) == b"PNG"
    assert calls == [{"url": URL, "timeout": 60, "follow_redirects": True}]
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("status", [500, 502, 503, 504, 599])
def test_download_5xx_then_success_retries_once(monkeypatch, capsys, status):
    calls = _get_seq(monkeypatch, [_resp(status, b"<html>error</html>"), _resp(200, b"PNG")])
    assert generator_mod._download(URL) == b"PNG"
    assert len(calls) == 2 and all(c["url"] == URL for c in calls)
    log = capsys.readouterr().out
    assert "[generate]" in log and "1/2" in log


def test_download_5xx_twice_raises_status_error(monkeypatch):
    calls = _get_seq(monkeypatch, [_resp(500, b"<html>"), _resp(503, b"<html>")])
    with pytest.raises(httpx.HTTPStatusError) as ei:
        generator_mod._download(URL)
    assert ei.value.response.status_code == 503        # 마지막 시도의 오류
    assert len(calls) == generator_mod.DOWNLOAD_ATTEMPTS


@pytest.mark.parametrize("status", [400, 403, 404, 410, 429, 499])
def test_download_4xx_raises_immediately_without_retry(monkeypatch, capsys, status):
    calls = _get_seq(monkeypatch, [_resp(status), _resp(200, b"PNG")])
    with pytest.raises(httpx.HTTPStatusError) as ei:
        generator_mod._download(URL)
    assert ei.value.response.status_code == status
    assert len(calls) == 1
    assert capsys.readouterr().out == ""


def test_download_follows_redirects(monkeypatch):
    """CDN 리다이렉트는 httpx 가 따라가게 한다 (기본값은 안 따라감)."""
    calls = _get_seq(monkeypatch, [_resp(200, b"PNG")])
    assert generator_mod._download(URL) == b"PNG"
    assert calls[0]["follow_redirects"] is True


def test_download_unfollowed_3xx_raises_without_retry(monkeypatch):
    """그래도 3xx 가 남으면(리다이렉트 한도 등) 4xx 처럼 즉시 실패 — 본문을 이미지로 넘기지 않는다."""
    calls = _get_seq(monkeypatch, [_resp(302), _resp(200, b"PNG")])
    with pytest.raises(httpx.HTTPStatusError):
        generator_mod._download(URL)
    assert len(calls) == 1


@pytest.mark.parametrize("exc", [
    httpx.ConnectError("refused"), httpx.ReadTimeout("slow"),
    httpx.RemoteProtocolError("eof"), httpx.ConnectTimeout("t/o"),
])
def test_download_transport_error_then_success(monkeypatch, capsys, exc):
    calls = _get_seq(monkeypatch, [exc, _resp(200, b"PNG")])
    assert generator_mod._download(URL) == b"PNG"
    assert len(calls) == 2
    assert "[generate]" in capsys.readouterr().out


def test_download_transport_error_twice_raises(monkeypatch):
    calls = _get_seq(monkeypatch, [httpx.ConnectError("a"), httpx.ReadTimeout("b")])
    with pytest.raises(httpx.ReadTimeout):
        generator_mod._download(URL)
    assert len(calls) == 2


def test_download_transport_then_5xx_raises_last(monkeypatch):
    _get_seq(monkeypatch, [httpx.ConnectError("a"), _resp(500)])
    with pytest.raises(httpx.HTTPStatusError):
        generator_mod._download(URL)


def test_download_5xx_then_4xx_raises_4xx(monkeypatch):
    calls = _get_seq(monkeypatch, [_resp(500), _resp(404), _resp(200, b"PNG")])
    with pytest.raises(httpx.HTTPStatusError) as ei:
        generator_mod._download(URL)
    assert ei.value.response.status_code == 404 and len(calls) == 2


@pytest.mark.parametrize("exc", [ValueError("x"), RuntimeError("y"), KeyError("z")])
def test_download_other_errors_not_retried(monkeypatch, exc):
    calls = _get_seq(monkeypatch, [exc, _resp(200, b"PNG")])
    with pytest.raises(type(exc)):
        generator_mod._download(URL)
    assert len(calls) == 1


def test_download_empty_200_body_is_returned_as_is(monkeypatch):
    """상태 코드만 본다 — 200 이면 본문 검증은 뒷단 몫."""
    _get_seq(monkeypatch, [_resp(200, b"")])
    assert generator_mod._download(URL) == b""


def test_generate_ai_5xx_html_page_is_not_returned_as_image(monkeypatch):
    """사건 회귀: CDN 500 HTML 이 이미지 바이트로 넘어가던 문제 — 두 번 다 500 이면 예외."""
    monkeypatch.setattr(generator_mod, "fal_client", FakeFalClient(out_url=URL))
    calls = _get_seq(monkeypatch, [_resp(500, b"<html>500</html>")] * 2)
    with pytest.raises(httpx.HTTPStatusError):
        _generate_ai(b"o", _preset())
    assert len(calls) == 2


def test_generate_ai_5xx_once_then_image(monkeypatch):
    monkeypatch.setattr(generator_mod, "fal_client", FakeFalClient(out_url=URL))
    _get_seq(monkeypatch, [_resp(500, b"<html>"), _resp(200, b"IMG")])
    assert _generate_ai(b"o", _preset()) == b"IMG"
