"""app.services.detector - 순수 함수 + `_call`(VLM 호출 래퍼) 트레이싱 투명성.

`_call`/`match_anchors` 는 `google.genai.Client` 를 새로 만들지만, detector 모듈
이 `from google import genai` 형태로 임포트하므로 `detector.genai` 이름 자체를
가짜 네임스페이스로 바꿔치기해서 실제 네트워크 없이 검증한다.
"""
import json

import pytest

import app.core.tracing as tracing
from app.services.detector import (
    _box, _call, _usage, all_preserved, bubbles, match_anchors,
)


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


# ── 순수 함수 (기존) ─────────────────────────────
def test_box_fixes_swapped_coords():                 # 회귀: 전치 사건
    b = _box({"x1": 600, "y1": 100, "x2": 300, "y2": 400})
    assert (b["x1"], b["y1"], b["x2"], b["y2"]) == (300, 100, 600, 400)


def test_bubbles_only_preserved_with_box():
    checks = [
        {"what": "a", "preserved": True,  "x1": 1, "y1": 1, "x2": 2, "y2": 2},
        {"what": "b", "preserved": False, "x1": 1, "y1": 1, "x2": 2, "y2": 2},
        {"what": "c", "preserved": True},
    ]
    assert [c["what"] for c in bubbles(checks)] == ["a"]


def test_all_preserved_empty_true():
    assert all_preserved([]) is True


def test_all_preserved_detects_false():
    assert all_preserved([{"preserved": True}, {"preserved": False}]) is False


def test_match_empty_result_all_missed():
    orig = [{"what": "얼룩", "where": "앞면"}]
    m = match_anchors(orig, [])
    assert m["missed"] == orig and m["new"] == [] and m["matched"] == []


# ── _usage() 엣지 케이스 ─────────────────────────
class _Usage:
    def __init__(self, prompt=None, candidates=None, total=None):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.total_token_count = total


class _Resp:
    def __init__(self, usage_metadata=None):
        self.usage_metadata = usage_metadata


def test_usage_returns_none_when_resp_has_no_usage_metadata():
    assert _usage(_Resp(usage_metadata=None)) is None


def test_usage_returns_none_when_usage_metadata_attr_missing_entirely():
    class NoUsageAttr:
        pass
    assert _usage(NoUsageAttr()) is None


def test_usage_maps_fields_when_present():
    resp = _Resp(usage_metadata=_Usage(prompt=10, candidates=5, total=15))
    assert _usage(resp) == {"input": 10, "output": 5, "total": 15}


def test_usage_defaults_falsy_fields_to_zero():
    """0 이나 None 이 오면(SDK 가 종종 그럼) 0 으로 방어."""
    resp = _Resp(usage_metadata=_Usage(prompt=None, candidates=0, total=None))
    assert _usage(resp) == {"input": 0, "output": 0, "total": 0}


# ── _call(): 가짜 genai 클라이언트 ────────────────
class FakeModels:
    def __init__(self, text, usage_metadata=None):
        self._text = text
        self._usage_metadata = usage_metadata
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        self.last_kwargs = kwargs
        return _Resp2(self._text, self._usage_metadata)


class _Resp2:
    def __init__(self, text, usage_metadata):
        self.text = text
        self.usage_metadata = usage_metadata


def _make_fake_genai(text, usage_metadata=None):
    """detector.genai 자리에 넣을 최소 네임스페이스: `.Client(api_key=...)` 가
    `.models.generate_content(**kw)` 를 갖는 객체를 리턴."""
    models = FakeModels(text, usage_metadata)

    class _Client:
        def __init__(self, **kw):
            self.models = models

    class _Genai:
        Client = _Client

    return _Genai(), models


def test_call_disabled_tracing_returns_parsed_json_unchanged(monkeypatch):
    import app.services.detector as detector
    fake_genai, models = _make_fake_genai(json.dumps({"item": "chair"}))
    monkeypatch.setattr(detector, "genai", fake_genai)

    data = _call(b"imgbytes", "prompt text", "classify")

    assert data == {"item": "chair"}
    assert models.last_kwargs["model"] == detector.settings.VLM_MODEL


def test_call_json_decode_error_returns_empty_dict(monkeypatch):
    import app.services.detector as detector
    fake_genai, _ = _make_fake_genai("not valid json{{{")
    monkeypatch.setattr(detector, "genai", fake_genai)

    data = _call(b"imgbytes", "prompt text", "detect")

    assert data == {}


def test_call_disabled_tracing_does_not_touch_fake_client_beyond_generate_content(monkeypatch):
    """트레이싱 비활성이면 obs 는 None 이라 `.update(...)` 를 절대 호출하지
    않는다(호출했다면 AttributeError 로 바로 터졌을 것 - 가드가 없다면)."""
    import app.services.detector as detector
    fake_genai, _ = _make_fake_genai(json.dumps({"ok": True}))
    monkeypatch.setattr(detector, "genai", fake_genai)

    data = _call(b"x", "p", "verify")   # obs=None 경로 - 예외 없이 통과해야 함
    assert data == {"ok": True}


def test_call_enabled_tracing_invokes_obs_update_with_output_and_usage(monkeypatch):
    import app.services.detector as detector

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

    usage = _Usage(prompt=3, candidates=4, total=7)
    fake_genai, _ = _make_fake_genai(json.dumps({"item": "lamp"}), usage_metadata=usage)
    monkeypatch.setattr(detector, "genai", fake_genai)

    data = _call(b"imgbytes", "prompt text", "classify")

    assert data == {"item": "lamp"}
    assert fake_lf.calls[0]["name"] == "classify"
    assert fake_lf.calls[0]["as_type"] == "generation"
    assert fake_lf.obs.update_calls == [{
        "output": {"item": "lamp"},
        "usage_details": {"input": 3, "output": 4, "total": 7},
    }]


def test_call_enabled_tracing_json_decode_error_updates_obs_with_empty_output(monkeypatch):
    import app.services.detector as detector

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
            self.obs = FakeObservation()
        def start_as_current_observation(self, **kw):
            return FakeObservationCM(self.obs)

    fake_lf = FakeLangfuseClient()
    monkeypatch.setattr(tracing, "_disabled", False, raising=False)
    monkeypatch.setattr(tracing, "_client", fake_lf, raising=False)

    fake_genai, _ = _make_fake_genai("not json", usage_metadata=None)
    monkeypatch.setattr(detector, "genai", fake_genai)

    data = _call(b"x", "p", "detect")

    assert data == {}
    assert fake_lf.obs.update_calls == [{"output": {}, "usage_details": None}]
