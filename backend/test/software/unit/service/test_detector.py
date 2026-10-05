"""app.services.ai.detector - 순수 함수 + `_call`(VLM 호출 래퍼) 트레이싱 투명성.

`_call` 은 공용 클라이언트 `app.core.vlm.get_client()` 를 쓰는데,
detector 모듈이 `from app.core.vlm import get_client` 로 이름을 가져오므로
`detector.get_client` 자체를 가짜 클라이언트를 돌려주는 함수로 바꿔치기해서
실제 네트워크 없이 검증한다.
"""
import json

import pytest

import app.core.tracing as tracing
from app.services.ai import detector
from app.services.ai.detector import (
    _box, _call, _usage, all_preserved, check_photo,
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


# ── _usage() 엣지 케이스 ─────────────────────────
class _Usage:
    def __init__(self, prompt=None, candidates=None, total=None):
        self.prompt_token_count = prompt
        self.candidates_token_count = candidates
        self.total_token_count = total


class _Resp:
    def __init__(self, usage_metadata=None):
        self.usage_metadata = usage_metadata


def test_usage_folds_thinking_tokens_into_output():
    """생각 토큰은 출력 단가로 청구되므로 output 에 합친다."""
    u = _Usage(prompt=10, candidates=5, total=115)
    u.thoughts_token_count = 100
    assert _usage(_Resp(usage_metadata=u)) == {"input": 10, "output": 105, "thoughts": 100, "total": 115}


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


def test_call_json_decode_error_returns_empty_dict(monkeypatch):
    import app.services.ai.detector as detector
    fake_get_client, _ = _make_fake_get_client("not valid json{{{")
    monkeypatch.setattr(detector, "get_client", fake_get_client)

    data = _call(b"imgbytes", "prompt text", "detect")

    assert data == {}


def test_check_photo_vlm_exception_open_fallback(monkeypatch):
    """VLM 호출 자체가 예외를 던져도 재생성 루프에 태우지 않도록 valid=True 로
    개방형 폴백."""
    import app.services.ai.detector as detector

    def _boom(*a, **kw):
        raise RuntimeError("network down")
    monkeypatch.setattr(detector, "_call", _boom)

    result = check_photo(b"imgbytes")

    assert result == {"valid": True, "reason": ""}


def test_from_box_2d_maps_gemini_order_to_xy():
    """box_2d 는 [ymin, xmin, ymax, xmax] — y 가 먼저 (가로세로 전치 버그 방지)."""
    from app.services.ai.detector import _from_box_2d
    c = _from_box_2d({"what": "bear", "preserved": True, "box_2d": [493, 125, 851, 615]})
    assert (c["x1"], c["y1"], c["x2"], c["y2"]) == (125, 493, 615, 851)
    assert "box_2d" not in c


def test_all_preserved_fails_when_fewer_checks_than_anchors():
    """VLM 이 빈 응답/일부만 답하면 확인 못 한 앵커가 있으니 통과가 아니다."""
    assert all_preserved([], expected=2) is False
    assert all_preserved([{"preserved": True}], expected=2) is False
    assert all_preserved([{"preserved": True}, {"preserved": True}], expected=2) is True


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


# ── read_item_text(strict=...) : 가드용 결과 읽기 ──
@pytest.mark.parametrize("resp", [{}, {"texts": None},])
def test_read_item_text_strict_raises_without_texts_list(monkeypatch, resp):
    from app.services.ai import detector
    monkeypatch.setattr(detector, "_call", lambda *a, **k: resp)
    with pytest.raises(ValueError):
        detector.read_item_text(b"img", "shoe", strict=True)


# ── 호출 이름별 이미지 해상도 · 모델 (app.core.vlm) ──
from app.core.config import settings as _settings  # noqa: E402


@pytest.fixture()
def vlm_defaults(monkeypatch):
    """.env 덮어쓰기 없이 코드 기본값만."""
    monkeypatch.setattr(_settings, "vlm_media_resolution", {})
    monkeypatch.setattr(_settings, "vlm_models", {})
    monkeypatch.setattr(_settings, "VLM_MODEL", "base-model")


def test_call_uses_per_call_model_override(monkeypatch, vlm_defaults):
    import app.services.ai.detector as detector
    monkeypatch.setattr(_settings, "vlm_models", {"classify": "lite-model"})
    fake_get_client, models = _make_fake_get_client(json.dumps({"ok": True}))
    monkeypatch.setattr(detector, "get_client", fake_get_client)
    _call(b"i", "p", "classify")
    assert models.last_kwargs["model"] == "lite-model"
    _call(b"i", "p", "detect")
    assert models.last_kwargs["model"] == "base-model"


# ══ analyze(): 파이프라인 첫 단계 (VLM 1회, 예전 classify + detect) ══
_ANALYZE_FULL = {
    "item": "  electric shaver ", "considered": ["logo", " model text ", ""],
    "item_box_2d": [100, 200, 700, 800],
    "photo_type": "product", "wear_level": "none", "watermark": "background",
    "text_level": "simple",
    "marks": [{"what": " BRAUN ", "where": " front "}, {"what": "Series 9", "where": "side"}],
}


def _analyze_with(monkeypatch, resp):
    seen = []
    monkeypatch.setattr(detector, "_call",
                        lambda img, prompt, name="": seen.append((img, name)) or resp)
    return seen


def test_analyze_parses_full_response(monkeypatch):
    seen = _analyze_with(monkeypatch, _ANALYZE_FULL)
    out = detector.analyze(b"img")
    assert seen == [(b"img", "analyze")]          # VLM 1회, 호출 이름 "analyze"
    assert out == {
        "item": "electric shaver",
        "considered": ["logo", "model text"],       # 빈 문자열 제외, strip
        "anchors": [{"category": "print", "what": "BRAUN", "where": "front"},
                    {"category": "print", "what": "Series 9", "where": "side"}],
        # box_2d = [ymin, xmin, ymax, xmax] → x1=xmin ...
        "item_box": {"x1": 200, "y1": 100, "x2": 800, "y2": 700},
        "photo_type": "product", "wear_level": "none", "watermark": "background",
        "text_level": "simple",
        "item_count": 1, "item_cut_off": False, "objects": [],
    }


@pytest.mark.parametrize("raw", [None, "", "unknown",])
def test_analyze_unknown_values_fall_back_to_defaults(monkeypatch, raw):
    resp = {"marks": [], "photo_type": raw, "wear_level": raw, "watermark": raw, "text_level": raw}
    _analyze_with(monkeypatch, resp)
    out = detector.analyze(b"x")
    assert out["photo_type"] == "product"
    assert out["wear_level"] == "light"
    assert out["watermark"] == "none"
    assert out["text_level"] == "simple"


@pytest.mark.parametrize("resp", [
    {},                                   # JSON 깨짐 → _call 이 {} 반환
    {"item": "cup", "text_level": "none"},   # marks 키 없음
    {"marks": None},])
def test_analyze_without_marks_list_raises(monkeypatch, resp):
    _analyze_with(monkeypatch, resp)
    with pytest.raises(ValueError, match="marks"):
        detector.analyze(b"x")


def test_analyze_propagates_call_exceptions(monkeypatch):
    """_call 예외는 삼키지 않는다 — 재시도/detect_failed 판단은 pipeline.analyze 몫."""
    def boom(*a, **k):
        raise RuntimeError("vlm down")
    monkeypatch.setattr(detector, "_call", boom)
    with pytest.raises(RuntimeError):
        detector.analyze(b"x")


def test_analyze_null_what_is_dropped_like_empty(monkeypatch):
    """JSON null 은 빈 값 — 'None' 이라는 마크가 되면 안 된다 (_text: None → "")."""
    _analyze_with(monkeypatch, {"text_level": "none",
                                "marks": [{"what": None, "where": None}],
                                "considered": [None, "logo"]})
    out = detector.analyze(b"x")
    assert out["anchors"] == []
    assert out["text_level"] == "none"
    assert out["considered"] == ["logo"]


@pytest.mark.parametrize("raw", [
    # 예전 scene 값·옛 플래그 이름은 photo_type 으로 인정하지 않는다
    "partial_view", "single_item", "multiple_items",])
def test_analyze_photo_type_unknown_falls_back_to_product_and_logs(monkeypatch, caplog, raw):
    _analyze_with(monkeypatch, {"marks": [], "photo_type": raw})
    assert detector.analyze(b"x")["photo_type"] == "product"
    assert "photo_type 없음/모름" in caplog.text


def test_analyze_item_cut_off_missing_is_false(monkeypatch):
    """옛 프롬프트 응답(키 없음)은 예전 동작 — 잘리지 않은 것으로."""
    _analyze_with(monkeypatch, {"marks": []})
    assert detector.analyze(b"x")["item_cut_off"] is False


@pytest.mark.parametrize("texts", ["missing", None,])
def test_analyze_without_texts_list_leaves_key_out(monkeypatch, texts):
    """옛 프롬프트(texts 없음) · 이상한 값이면 키가 없다 — read_text 가 예전처럼 따로 읽는다."""
    resp = dict(_ANALYZE_FULL) if texts == "missing" else {**_ANALYZE_FULL, "texts": texts}
    _analyze_with(monkeypatch, resp)
    assert "item_texts" not in detector.analyze(b"img")


def test_analyze_parses_objects_for_sell_choice(monkeypatch):
    """10-03: 사진 속 물건 목록 — 사용자가 팔 물건을 고른다. 이름 · 박스가 이상한 항목은 뺀다."""
    _analyze_with(monkeypatch, {"marks": [], "objects": [
        {"what": " CD ", "box_2d": [100, 50, 900, 450], "for_sale": True},
        {"what": "CD", "box_2d": [100, 550, 900, 950]},                       # for_sale 없음 → True
        {"what": "keyboard", "box_2d": [0, 0, 80, 1000], "for_sale": False},
        {"what": "", "box_2d": [0, 0, 10, 10]}, {"what": "bad box", "box_2d": [1, 2]}, "junk",
        *({"what": f"o{i}", "box_2d": [0, 0, 10, 10]} for i in range(20))]})
    objs = detector.analyze(b"x")["objects"]
    assert objs[0] == {"what": "CD", "box": {"x1": 50, "y1": 100, "x2": 450, "y2": 900}, "for_sale": True}
    assert objs[1]["for_sale"] is True and objs[2] == {"what": "keyboard", "for_sale": False,
                                                        "box": {"x1": 0, "y1": 0, "x2": 1000, "y2": 80}}
    assert len(objs) == detector.MAX_OBJECTS and all(o["what"] for o in objs)


# ── 10-03: _parse_objects 직접 (위 analyze 경유 테스트와 겹치지 않는 경계) ──
from app.services.ai.detector import _parse_objects  # noqa: E402


@pytest.mark.parametrize("raw,expected", [
    (False, False), (True, True), (None, True),])
def test_parse_objects_for_sale_false_like_values(raw, expected):
    """for_sale 은 False · 0 · "false" 면 팔지 않는 물건, 없거나 그 밖이면 팔 물건 (기본 체크)."""
    out = _parse_objects([{"what": "a", "box_2d": [0, 0, 10, 10], "for_sale": raw}])
    assert out[0]["for_sale"] is expected




# ── confidence (10-04) ─────────────────────────
@pytest.mark.parametrize("conf,expected", [
    (0.8, 0.8), (1, 1.0), (True, None), ("0.9", None), (1.5, None),
])
def test_check_photo_keeps_only_valid_confidence(monkeypatch, conf, expected):
    monkeypatch.setattr(detector, "_call", lambda *a, **k: {
        "valid": True, "reason": "", "confidence": conf})
    out = check_photo(b"img")
    assert out.get("confidence") == expected
    if expected is not None:
        assert type(out["confidence"]) is float


# ── verify_combined (10-05): checks 깨지면 예외, added 만 깨지면 None ──
def test_verify_combined_parses_checks_and_added(monkeypatch):
    monkeypatch.setattr(detector, "_call_pair", lambda *a, **k: {
        "checks": [{"what": "ACME", "preserved": "true"}, "junk"],
        "added": [{"what": "H4", "where": "chest"}, {"what": ""}]})
    checks, added = detector.verify_combined(b"o", b"r", [{"what": "ACME", "where": "x"}])
    assert [c["preserved"] for c in checks] == [True]
    assert [a["what"] for a in added] == ["H4"]


@pytest.mark.parametrize("resp", [{}, {"checks": None, "added": []}, []])
def test_verify_combined_without_checks_raises(monkeypatch, resp):
    monkeypatch.setattr(detector, "_call_pair", lambda *a, **k: resp)
    with pytest.raises(ValueError):
        detector.verify_combined(b"o", b"r", [{"what": "a", "where": "b"}])


def test_verify_combined_bad_added_is_none(monkeypatch):
    monkeypatch.setattr(detector, "_call_pair", lambda *a, **k: {
        "checks": [{"what": "a", "preserved": True}], "added": "none"})
    assert detector.verify_combined(b"o", b"r", [{"what": "a", "where": "b"}])[1] is None
