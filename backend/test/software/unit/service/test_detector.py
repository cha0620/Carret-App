"""app.services.ai.detector - 순수 함수 + `_call`(VLM 호출 래퍼) 트레이싱 투명성.

`_call`/`match_anchors` 는 공용 클라이언트 `app.core.vlm.get_client()` 를 쓰는데,
detector 모듈이 `from app.core.vlm import get_client` 로 이름을 가져오므로
`detector.get_client` 자체를 가짜 클라이언트를 돌려주는 함수로 바꿔치기해서
실제 네트워크 없이 검증한다.
"""
import json

import pytest

import app.core.tracing as tracing
from app.services.ai.detector import (
    _box, _call, _usage, all_preserved, bubbles, check_photo, match_anchors,
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
    assert all_preserved([], expected=0) is True


def test_all_preserved_detects_false():
    assert all_preserved([{"preserved": True}, {"preserved": False}], expected=2) is False


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
    assert _usage(resp) == {"input": 10, "output": 5, "thoughts": 0, "total": 15}


def test_usage_folds_thinking_tokens_into_output():
    """생각 토큰은 출력 단가로 청구되므로 output 에 합친다."""
    u = _Usage(prompt=10, candidates=5, total=115)
    u.thoughts_token_count = 100
    assert _usage(_Resp(usage_metadata=u)) == {"input": 10, "output": 105, "thoughts": 100, "total": 115}


def test_usage_defaults_falsy_fields_to_zero():
    """0 이나 None 이 오면(SDK 가 종종 그럼) 0 으로 방어."""
    resp = _Resp(usage_metadata=_Usage(prompt=None, candidates=0, total=None))
    assert _usage(resp) == {"input": 0, "output": 0, "thoughts": 0, "total": 0}


# ── _call(): 가짜 VLM 클라이언트 ────────────────
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


def _make_fake_get_client(text, usage_metadata=None):
    """detector.get_client 자리에 넣을 함수: 호출하면 `.models.generate_content(**kw)`
    를 갖는 가짜 클라이언트를 리턴."""
    models = FakeModels(text, usage_metadata)

    class _Client:
        def __init__(self):
            self.models = models

    client = _Client()
    return (lambda: client), models


def test_call_disabled_tracing_returns_parsed_json_unchanged(monkeypatch):
    import app.services.ai.detector as detector
    fake_get_client, models = _make_fake_get_client(json.dumps({"item": "chair"}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)

    data = _call(b"imgbytes", "prompt text", "detect")

    assert data == {"item": "chair"}
    assert models.last_kwargs["model"] == detector.settings.VLM_MODEL


def test_call_json_decode_error_returns_empty_dict(monkeypatch):
    import app.services.ai.detector as detector
    fake_get_client, _ = _make_fake_get_client("not valid json{{{")
    monkeypatch.setattr(detector, "get_client", fake_get_client)

    data = _call(b"imgbytes", "prompt text", "detect")

    assert data == {}


def test_call_disabled_tracing_does_not_touch_fake_client_beyond_generate_content(monkeypatch):
    """트레이싱 비활성이면 obs 는 None 이라 `.update(...)` 를 절대 호출하지
    않는다(호출했다면 AttributeError 로 바로 터졌을 것 - 가드가 없다면)."""
    import app.services.ai.detector as detector
    fake_get_client, _ = _make_fake_get_client(json.dumps({"ok": True}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)

    data = _call(b"x", "p", "verify")   # obs=None 경로 - 예외 없이 통과해야 함
    assert data == {"ok": True}


def test_call_enabled_tracing_invokes_obs_update_with_output_and_usage(monkeypatch):
    import app.services.ai.detector as detector

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
    fake_get_client, _ = _make_fake_get_client(json.dumps({"item": "lamp"}), usage_metadata=usage)
    monkeypatch.setattr(detector, "get_client", fake_get_client)

    data = _call(b"imgbytes", "prompt text", "classify")

    assert data == {"item": "lamp"}
    assert fake_lf.calls[0]["name"] == "classify"
    assert fake_lf.calls[0]["as_type"] == "generation"
    assert fake_lf.obs.update_calls == [{
        "output": {"item": "lamp"},
        "usage_details": {"input": 3, "output": 4, "thoughts": 0, "total": 7},
    }]


def test_call_enabled_tracing_json_decode_error_updates_obs_with_empty_output(monkeypatch):
    import app.services.ai.detector as detector

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

    fake_get_client, _ = _make_fake_get_client("not json", usage_metadata=None)
    monkeypatch.setattr(detector, "get_client", fake_get_client)

    data = _call(b"x", "p", "detect")

    assert data == {}
    assert fake_lf.obs.update_calls == [{"output": {}, "usage_details": None}]


# ── check_photo(): 재생성 게이트 ──────────────────
def test_check_photo_valid_true(monkeypatch):
    import app.services.ai.detector as detector
    fake_get_client, _ = _make_fake_get_client(json.dumps({"valid": True, "reason": ""}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)

    result = check_photo(b"imgbytes")

    assert result == {"valid": True, "reason": ""}


def test_check_photo_valid_false_cropped_or_overlay(monkeypatch):
    import app.services.ai.detector as detector
    fake_get_client, _ = _make_fake_get_client(
        json.dumps({"valid": False, "reason": "caption text covers the product"})
    )
    monkeypatch.setattr(detector, "get_client", fake_get_client)

    result = check_photo(b"imgbytes")

    assert result == {"valid": False, "reason": "caption text covers the product"}


def test_check_photo_vlm_exception_open_fallback(monkeypatch):
    """VLM 호출 자체가 예외를 던져도 재생성 루프에 태우지 않도록 valid=True 로
    개방형 폴백."""
    import app.services.ai.detector as detector

    def _boom(*a, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(detector, "_call", _boom)

    result = check_photo(b"imgbytes")

    assert result == {"valid": True, "reason": ""}


def test_check_photo_malformed_json_falls_through_to_valid_true(monkeypatch):
    """_call 이 JSON 파싱 실패 시 {} 를 반환하는 기존 경로 → check_photo 는
    data.get("valid", True) 로 True 에 안전 착지."""
    import app.services.ai.detector as detector
    fake_get_client, _ = _make_fake_get_client("not valid json{{{")
    monkeypatch.setattr(detector, "get_client", fake_get_client)

    result = check_photo(b"imgbytes")

    assert result == {"valid": True, "reason": ""}


def test_from_box_2d_maps_gemini_order_to_xy():
    """box_2d 는 [ymin, xmin, ymax, xmax] — y 가 먼저 (가로세로 전치 버그 방지)."""
    from app.services.ai.detector import _from_box_2d
    c = _from_box_2d({"what": "bear", "preserved": True, "box_2d": [493, 125, 851, 615]})
    assert (c["x1"], c["y1"], c["x2"], c["y2"]) == (125, 493, 615, 851)
    assert "box_2d" not in c


def test_from_box_2d_keeps_legacy_xy_keys():
    from app.services.ai.detector import _from_box_2d
    c = {"what": "a", "preserved": True, "x1": 1, "y1": 2, "x2": 3, "y2": 4}
    assert _from_box_2d(c) == c


def test_from_box_2d_ignores_malformed():
    from app.services.ai.detector import _from_box_2d
    for bad in ([1, 2, 3], ["1", 2, 3, 4], None, [True, 1, 2, 3]):
        c = {"what": "a", "preserved": True, "box_2d": bad}
        assert _from_box_2d(c) == c


def test_verify_and_locate_accepts_box_2d(monkeypatch):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: {"checks": [
        {"what": "logo", "preserved": True, "box_2d": [100, 200, 300, 400]},
        {"what": "stain", "preserved": False},
    ]})
    checks = detector.verify_and_locate(b"img", [{"what": "logo", "where": "front"}])
    assert (checks[0]["x1"], checks[0]["y1"], checks[0]["x2"], checks[0]["y2"]) == (200, 100, 400, 300)
    assert checks[1]["preserved"] is False


def test_zero_area_box_is_rejected():
    from app.services.ai.detector import _has_box
    assert not _has_box({"x1": 10, "y1": 10, "x2": 10, "y2": 50})
    assert _has_box({"x1": 10, "y1": 10, "x2": 20, "y2": 50})


def test_read_item_text_converts_boxes_and_drops_empty(monkeypatch):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: {
        "item_box_2d": [100, 200, 900, 800],
        "texts": [{"text": "ROLEX", "box_2d": [300, 400, 350, 600]},
                  {"text": "  "}, "junk", {"text": "29"}]})
    out = detector.read_item_text(b"img", "watch")
    assert out["item_box"] == {"x1": 200, "y1": 100, "x2": 800, "y2": 900}
    assert out["texts"][0] == {"text": "ROLEX", "x1": 400, "y1": 300, "x2": 600, "y2": 350}
    assert out["texts"][1] == {"text": "29"}
    assert len(out["texts"]) == 2


def test_read_item_text_without_item_box(monkeypatch):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: {"texts": []})
    assert detector.read_item_text(b"img") == {"item_box": None, "texts": []}


def test_all_preserved_fails_when_fewer_checks_than_anchors():
    """VLM 이 빈 응답/일부만 답하면 확인 못 한 앵커가 있으니 통과가 아니다."""
    assert all_preserved([], expected=2) is False
    assert all_preserved([{"preserved": True}], expected=2) is False
    assert all_preserved([{"preserved": True}, {"preserved": True}], expected=2) is True


def test_verify_keeps_preserved_check_without_box_for_gate(monkeypatch):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: {"checks": [
        {"what": "logo", "preserved": True},
        {"what": "stain", "preserved": True, "box_2d": [1, 2, 30, 40]},
    ]})
    checks = detector.verify_and_locate(b"img", [{"what": "logo", "where": "a"}, {"what": "stain", "where": "b"}])
    assert len(checks) == 2
    assert detector.all_preserved(checks, expected=2) is True
    assert [b["what"] for b in detector.bubbles(checks)] == ["stain"]   # 말풍선은 좌표 있는 것만


def test_verify_normalizes_string_bool_and_bad_boxes(monkeypatch):
    """"false" 문자열이 True 로 새면 게이트가 fail-open — bool 로 정규화, 못 쓰는 좌표는 지움."""
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: {"checks": [
        {"what": "stain", "preserved": "false"},
        {"what": 7, "preserved": "true", "x1": 1.5, "y1": 2, "x2": 3, "y2": 4},
        {"what": "no flag"},
        "junk",
    ]})
    checks = detector.verify_and_locate(b"img", [{"what": "a", "where": "b"}] * 3)
    assert [c["preserved"] for c in checks] == [False, True, False]
    assert checks[1]["what"] == "7" and "x1" not in checks[1]
    assert detector.all_preserved(checks, expected=3) is False


def test_verify_non_dict_response_is_empty(monkeypatch):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: [])
    assert detector.verify_and_locate(b"img", []) == []


def test_detect_drops_anchors_without_what(monkeypatch):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: {"defects": [
        {"what": "", "where": "x"}, {"what": "stain", "where": "sleeve"}]})
    assert [a["what"] for a in detector.detect_defects(b"img")] == ["stain"]



# ── detect_defects: 빈/깨진 응답 — strict 면 실패, 기본은 [] ──
MALFORMED = [
    {},                              # JSON 파싱 실패 → _call 이 {} 반환
    {"defects": None},
    {"defects": "none"},
    {"defects": {"what": "stain"}},
    {"other": []},
    [],
    None,
    "defects",
]


@pytest.mark.parametrize("resp", MALFORMED)
def test_detect_malformed_response_raises_when_strict(monkeypatch, resp):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: resp)
    with pytest.raises(ValueError):
        detector.detect_defects(b"img", strict=True)


@pytest.mark.parametrize("resp", MALFORMED)
def test_detect_malformed_response_returns_empty_by_default(monkeypatch, resp):
    """eval/dev 호출부(기본 strict=False)는 예전처럼 [] — 배치가 한 건에 멈추지 않게."""
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: resp)
    assert detector.detect_defects(b"img") == []
    assert detector.detect_defects(b"img", "chair", ["stain"], strict=False) == []


@pytest.mark.parametrize("strict", [False, True])
def test_detect_empty_defects_list_is_no_defects(monkeypatch, strict):
    """빈 목록은 '하자 없음' — strict 여도 예외가 아니다."""
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: {"defects": []})
    assert detector.detect_defects(b"img", strict=strict) == []


def test_detect_strict_valid_response_same_as_default(monkeypatch):
    from app.services.ai import detector
    resp = {"defects": [{"category": "stain", "what": "stain", "where": "sleeve"}]}
    monkeypatch.setattr(detector, "_call", lambda *a, **k: resp)
    assert detector.detect_defects(b"img", strict=True) == detector.detect_defects(b"img")
    assert detector.detect_defects(b"img", strict=True)[0]["what"] == "stain"


def test_detect_strict_is_keyword_only():
    from app.services.ai import detector
    with pytest.raises(TypeError):
        detector.detect_defects(b"img", "chair", [], True)


def test_detect_strict_error_message_truncated(monkeypatch):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: {"x": "y" * 1000})
    with pytest.raises(ValueError) as ei:
        detector.detect_defects(b"img", strict=True)
    assert len(str(ei.value)) < 300


def test_detect_unknown_category_becomes_other(monkeypatch):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: {"defects": [
        {"category": "alien", "what": "dent", "where": "top"}]})
    assert detector.detect_defects(b"img")[0]["category"] == "other"


# ── read_item_text(strict=...) : 가드용 결과 읽기 ──
@pytest.mark.parametrize("resp", [{}, {"texts": None}, {"texts": "NIKE"},
                                  {"texts": {"text": "a"}}, {"item_box_2d": [0, 0, 1, 1]}])
def test_read_item_text_strict_raises_without_texts_list(monkeypatch, resp):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: resp)
    with pytest.raises(ValueError):
        detector.read_item_text(b"img", "shoe", strict=True)


@pytest.mark.parametrize("resp", [{}, {"item_box_2d": None}])
def test_read_item_text_default_tolerates_missing_texts(monkeypatch, resp):
    """read_text(원본) 경로는 기본값 — 빈 응답이면 글자 없음으로 진행."""
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: resp)
    assert detector.read_item_text(b"img")["texts"] == []


def test_read_item_text_strict_empty_list_is_no_text(monkeypatch):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: {"texts": []})
    assert detector.read_item_text(b"img", strict=True)["texts"] == []


def test_read_item_text_strict_valid_same_as_default(monkeypatch):
    from app.services.ai import detector
    resp = {"texts": [{"text": " NIKE "}, {"text": ""}, "junk"]}
    monkeypatch.setattr(detector, "_call", lambda *a, **k: resp)
    assert detector.read_item_text(b"img", strict=True) == detector.read_item_text(b"img")
    assert [t["text"] for t in detector.read_item_text(b"img", strict=True)["texts"]] == ["NIKE"]


def test_read_item_text_strict_is_keyword_only():
    from app.services.ai import detector
    with pytest.raises(TypeError):
        detector.read_item_text(b"img", "shoe", True)


# ── match_anchors(): 공용 get_client 경로 ────────────────
def test_match_anchors_uses_shared_client_and_maps_indices(monkeypatch):
    """비어 있지 않은 입력이면 get_client() 의 클라이언트로 VLM 을 부르고,
    matches[j] 인덱스로 matched/missed/new 를 나눈다 (범위 밖·비정수는 new)."""
    import app.services.ai.detector as detector
    fake_get_client, models = _make_fake_get_client(json.dumps({"matches": [1, -1, 7]}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)
    orig = [{"what": "a", "where": "x"}, {"what": "b", "where": "y"}]
    result = [{"what": "B", "where": "y"}, {"what": "c", "where": "z"},
              {"what": "d", "where": "w"}]

    m = match_anchors(orig, result)

    assert models.last_kwargs["model"] == detector.settings.VLM_MODEL
    assert m["matched"] == [(orig[1], result[0])]
    assert m["new"] == [result[1], result[2]]
    assert m["missed"] == [orig[0]]


def test_match_anchors_bad_json_treats_all_as_new(monkeypatch):
    import app.services.ai.detector as detector
    fake_get_client, _ = _make_fake_get_client("{{nope")
    monkeypatch.setattr(detector, "get_client", fake_get_client)
    orig = [{"what": "a", "where": "x"}]
    result = [{"what": "b", "where": "y"}]

    m = match_anchors(orig, result)

    assert m == {"matched": [], "missed": orig, "new": result}


# ── 호출 이름별 이미지 해상도 · 모델 (app.core.vlm) ──
from app.core.config import settings as _settings  # noqa: E402


def _res_level(part):
    r = part.media_resolution
    return None if r is None else str(getattr(r.level, "value", r.level))


@pytest.fixture()
def vlm_defaults(monkeypatch):
    """.env 덮어쓰기 없이 코드 기본값만."""
    monkeypatch.setattr(_settings, "vlm_media_resolution", {})
    monkeypatch.setattr(_settings, "vlm_models", {})
    monkeypatch.setattr(_settings, "VLM_MODEL", "base-model")


@pytest.mark.parametrize("name,expected", [
    ("classify", "MEDIA_RESOLUTION_LOW"),
    ("check_photo", "MEDIA_RESOLUTION_LOW"),
    ("detect", None),
    ("verify", None),
    ("vlm_call", None),
])
def test_call_image_part_resolution_by_call_name(monkeypatch, vlm_defaults, name, expected):
    import app.services.ai.detector as detector
    fake_get_client, models = _make_fake_get_client(json.dumps({"ok": True}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)

    _call(b"imgbytes", "prompt", name)

    part, prompt = models.last_kwargs["contents"]
    assert prompt == "prompt"
    assert part.inline_data.data == b"imgbytes" and part.inline_data.mime_type == "image/jpeg"
    assert _res_level(part) == expected


def test_call_image_resolution_follows_settings_override(monkeypatch, vlm_defaults):
    import app.services.ai.detector as detector
    monkeypatch.setattr(_settings, "vlm_media_resolution", {"detect": "medium", "classify": "default"})
    fake_get_client, models = _make_fake_get_client(json.dumps({"ok": True}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)
    _call(b"i", "p", "detect")
    assert _res_level(models.last_kwargs["contents"][0]) == "MEDIA_RESOLUTION_MEDIUM"
    _call(b"i", "p", "classify")
    assert _res_level(models.last_kwargs["contents"][0]) is None


def test_call_uses_per_call_model_override(monkeypatch, vlm_defaults):
    import app.services.ai.detector as detector
    monkeypatch.setattr(_settings, "vlm_models", {"classify": "lite-model"})
    fake_get_client, models = _make_fake_get_client(json.dumps({"ok": True}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)
    _call(b"i", "p", "classify")
    assert models.last_kwargs["model"] == "lite-model"
    _call(b"i", "p", "detect")
    assert models.last_kwargs["model"] == "base-model"


@pytest.mark.parametrize("blank", ["", "  "])
def test_call_blank_model_override_falls_back(monkeypatch, vlm_defaults, blank):
    import app.services.ai.detector as detector
    monkeypatch.setattr(_settings, "vlm_models", {"classify": blank})
    fake_get_client, models = _make_fake_get_client(json.dumps({"ok": True}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)
    _call(b"i", "p", "classify")
    assert models.last_kwargs["model"] == "base-model"


def test_call_observe_model_matches_generate_content_model(monkeypatch, vlm_defaults):
    import contextlib
    import app.services.ai.detector as detector
    monkeypatch.setattr(_settings, "vlm_models", {"classify": "lite-model"})
    seen = []

    @contextlib.contextmanager
    def fake_observe(name, **kw):
        seen.append((name, kw.get("model")))
        yield None
    monkeypatch.setattr(detector, "observe", fake_observe)
    fake_get_client, models = _make_fake_get_client(json.dumps({"ok": True}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)
    _call(b"i", "p", "classify")
    assert seen == [("classify", "lite-model")] and models.last_kwargs["model"] == "lite-model"


def test_match_anchors_uses_match_model_and_no_image(monkeypatch, vlm_defaults):
    import app.services.ai.detector as detector
    monkeypatch.setattr(_settings, "vlm_models", {"match": "match-model", "classify": "x"})
    fake_get_client, models = _make_fake_get_client(
        json.dumps({"matched": [], "missed": [], "new": []}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)
    match_anchors([{"what": "a", "where": "b"}], [{"what": "c", "where": "d"}])
    assert models.last_kwargs["model"] == "match-model"
    assert all(isinstance(c, str) for c in models.last_kwargs["contents"])


def test_match_anchors_without_override_uses_base_model(monkeypatch, vlm_defaults):
    import app.services.ai.detector as detector
    fake_get_client, models = _make_fake_get_client(
        json.dumps({"matched": [], "missed": [], "new": []}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)
    match_anchors([{"what": "a", "where": "b"}], [{"what": "c", "where": "d"}])
    assert models.last_kwargs["model"] == "base-model"


# ══ detect_full: 하자 + text_level + item_box (VLM 1회) ══
_STAIN = {"category": "surface_damage", "what": "stain", "where": "sleeve"}
_PRINT = {"category": "print", "what": "logo: NIKE", "where": "chest"}


def _full(monkeypatch, resp, **kw):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: resp)
    return detector.detect_full(b"img", **kw)


def test_detect_full_text_levels_constant():
    from app.services.ai import detector
    assert detector.TEXT_LEVELS == ("none", "simple", "dense")


@pytest.mark.parametrize("level", ["none", "simple", "dense"])
def test_detect_full_known_text_level_kept(monkeypatch, capsys, level):
    out = _full(monkeypatch, {"defects": [_STAIN], "text_level": level})
    assert out["text_level"] == level
    assert "text_level" not in capsys.readouterr().out   # 정상 값은 로그 없음


@pytest.mark.parametrize("raw,expected", [
    (" DENSE ", "dense"), ("Simple", "simple"), ("NONE\n", "none"), ("\tdense", "dense"),
])
def test_detect_full_text_level_case_and_whitespace(monkeypatch, raw, expected):
    assert _full(monkeypatch, {"defects": [], "text_level": raw})["text_level"] == expected


@pytest.mark.parametrize("raw", ["lots", "medium", "", "   ", None, 3, 0, False,
                                 ["dense"], {"level": "dense"}, "dense text"])
def test_detect_full_unknown_text_level_is_simple_and_logged(monkeypatch, capsys, raw):
    out = _full(monkeypatch, {"defects": [], "text_level": raw})
    assert out["text_level"] == "simple"
    log = capsys.readouterr().out
    assert "[detect]" in log and "text_level" in log and repr(raw) in log


def test_detect_full_missing_text_level_is_simple_and_logged(monkeypatch, capsys):
    out = _full(monkeypatch, {"defects": [_STAIN]})
    assert out["text_level"] == "simple"
    log = capsys.readouterr().out
    assert "[detect]" in log and "None" in log     # 빠진 값은 None 으로 찍힌다


def test_detect_full_none_with_print_anchor_becomes_simple(monkeypatch, capsys):
    """모순(글자 없음 + print 하자) — 글자 보호를 끄지 않도록 simple."""
    out = _full(monkeypatch, {"defects": [_STAIN, _PRINT], "text_level": "none"})
    assert out["text_level"] == "simple"
    assert [a["category"] for a in out["anchors"]] == ["surface_damage", "print"]


def test_detect_full_none_with_print_anchor_case_insensitive_level(monkeypatch):
    assert _full(monkeypatch, {"defects": [_PRINT], "text_level": " None "})["text_level"] == "simple"


@pytest.mark.parametrize("defects", [
    [],
    [_STAIN],
    [{"category": "other", "what": "print on chest", "where": "x"}],   # 카테고리가 print 가 아님
    [{"category": "print", "what": "", "where": "x"}],                 # what 없음 → 앵커에서 빠짐
    [{"category": "PRINT", "what": "logo", "where": "x"}],             # 목록 밖 → other
])
def test_detect_full_none_without_print_anchor_stays_none(monkeypatch, defects):
    assert _full(monkeypatch, {"defects": defects, "text_level": "none"})["text_level"] == "none"


@pytest.mark.parametrize("level", ["simple", "dense"])
def test_detect_full_print_anchor_does_not_change_other_levels(monkeypatch, level):
    assert _full(monkeypatch, {"defects": [_PRINT], "text_level": level})["text_level"] == level


def test_detect_full_unknown_level_with_print_anchor_is_simple(monkeypatch):
    assert _full(monkeypatch, {"defects": [_PRINT], "text_level": "?"})["text_level"] == "simple"


def test_detect_full_item_box_converted_from_gemini_order(monkeypatch):
    out = _full(monkeypatch, {"defects": [], "text_level": "none",
                              "item_box_2d": [100, 200, 300, 400]})
    assert out["item_box"] == {"x1": 200, "y1": 100, "x2": 400, "y2": 300}


def test_detect_full_item_box_swapped_corners_sorted(monkeypatch):
    out = _full(monkeypatch, {"defects": [], "item_box_2d": [900, 800, 100, 50]})
    assert out["item_box"] == {"x1": 50, "y1": 100, "x2": 800, "y2": 900}


def test_detect_full_item_box_floats_rounded(monkeypatch):
    out = _full(monkeypatch, {"defects": [], "item_box_2d": [10.4, 20.6, 300.5, 999.9]})
    assert out["item_box"] == {"x1": 21, "y1": 10, "x2": 1000, "y2": 300}


@pytest.mark.parametrize("box", [
    None, [], [1, 2, 3], [1, 2, 3, 4, 5], "0,0,10,10", {"ymin": 0},
    ["0", "0", "10", "10"], [True, 0, 10, 10],
    [0, 0, 1001, 10],        # 범위 밖
    [-1, 0, 10, 10],
    [0, 100, 500, 100],      # 폭 0
    [200, 0, 200, 500],      # 높이 0
    [None, 0, 10, 10],
])
def test_detect_full_invalid_item_box_is_none(monkeypatch, box):
    out = _full(monkeypatch, {"defects": [_STAIN], "text_level": "none", "item_box_2d": box})
    assert out["item_box"] is None
    assert out["anchors"][0]["what"] == "stain"     # 박스가 깨져도 하자는 살린다


def test_detect_full_missing_item_box_is_none(monkeypatch):
    assert _full(monkeypatch, {"defects": []})["item_box"] is None


def test_detect_full_ignores_legacy_item_box_key(monkeypatch):
    """item_box_2d 만 읽는다 — x1.. 키로 준 item_box 는 쓰지 않는다."""
    out = _full(monkeypatch, {"defects": [], "item_box": {"x1": 1, "y1": 1, "x2": 9, "y2": 9}})
    assert out["item_box"] is None


@pytest.mark.parametrize("resp", MALFORMED)
def test_detect_full_malformed_raises_when_strict(monkeypatch, resp):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: resp)
    with pytest.raises(ValueError):
        detector.detect_full(b"img", strict=True)


@pytest.mark.parametrize("resp", MALFORMED + [{"text_level": "dense",
                                                "item_box_2d": [0, 0, 500, 500]}])
def test_detect_full_malformed_default_is_empty_simple_no_box(monkeypatch, resp):
    """defects 가 없으면 다른 필드가 멀쩡해도 버린다 (응답 전체를 못 믿음)."""
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: resp)
    assert detector.detect_full(b"img") == {"anchors": [], "text_level": "simple",
                                            "item_box": None}


def test_detect_full_strict_valid_response_ok(monkeypatch):
    out = _full(monkeypatch, {"defects": [], "text_level": "dense"}, strict=True)
    assert out == {"anchors": [], "text_level": "dense", "item_box": None}


def test_detect_full_strict_is_keyword_only():
    from app.services.ai import detector
    with pytest.raises(TypeError):
        detector.detect_full(b"img", "chair", [], True)


def test_detect_full_calls_detect_prompt_with_item_and_hints(monkeypatch):
    from app.services.ai import detector
    seen = []
    monkeypatch.setattr(detector.P, "detect_prompt",
                        lambda item, considered: seen.append((item, considered)) or "PROMPT")
    monkeypatch.setattr(detector, "_call",
                        lambda img, prompt, name="": seen.append((img, prompt, name)) or
                        {"defects": []})
    detector.detect_full(b"img", "mug", ["chip"])
    detector.detect_full(b"img2")
    assert seen == [("mug", ["chip"]), (b"img", "PROMPT", "detect"),
                    ("object", []), (b"img2", "PROMPT", "detect")]


def test_detect_defects_is_detect_full_anchors(monkeypatch):
    from app.services.ai import detector
    resp = {"defects": [_STAIN, _PRINT], "text_level": "dense",
            "item_box_2d": [0, 0, 500, 500]}
    monkeypatch.setattr(detector, "_call", lambda *a, **k: resp)
    assert detector.detect_defects(b"img", strict=True) == detector.detect_full(b"img")["anchors"]
    assert isinstance(detector.detect_defects(b"img"), list)


def test_detect_defects_forwards_args_to_detect_full(monkeypatch):
    from app.services.ai import detector
    seen = []
    monkeypatch.setattr(detector, "detect_full",
                        lambda img, item, considered, *, strict: seen.append(
                            (img, item, considered, strict)) or {"anchors": ["A"]})
    assert detector.detect_defects(b"i", "cup", ["x"], strict=True) == ["A"]
    assert seen == [(b"i", "cup", ["x"], True)]


# ── classify: 목록으로 감싼 응답 (lite 모델) ──
@pytest.mark.parametrize("raw, item", [
    ([{"item": "stapler", "considered": ["rust"]}], "stapler"),   # 객체 하나를 목록으로
    ([], "object"),                                                # 빈 목록 → 폴백
    (["stapler"], "object"),                                       # 객체가 아닌 목록 → 폴백
])
def test_classify_accepts_list_wrapped_response(monkeypatch, raw, item):
    import app.services.ai.detector as detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: raw)
    assert detector.classify(b"img")["item"] == item
