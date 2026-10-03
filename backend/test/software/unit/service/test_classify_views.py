"""detector.classify_views — 여러 장을 VLM 1회로 (낮은 해상도 "views") + 응답 정규화.

test_detector.py 처럼 detector.get_client 를 가짜 클라이언트로 바꿔치기한다 (실제 호출 없음).
views_prompt(n) · vlm 의 "views" 설정도 여기서 본다.
"""
import json

import pytest

import app.core.tracing as tracing
import app.prompts as P
from app.core import vlm
from app.core.config import settings
from app.services.ai import detector


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)


@pytest.fixture(autouse=True)
def vlm_defaults(monkeypatch):
    """.env 덮어쓰기 없이 코드 기본값만."""
    monkeypatch.setattr(settings, "vlm_media_resolution", {})
    monkeypatch.setattr(settings, "vlm_models", {})
    monkeypatch.setattr(settings, "vlm_thinking", {})
    monkeypatch.setattr(settings, "VLM_MODEL", "base-model")


class _Models:
    def __init__(self, text):
        self.text = text
        self.calls = 0
        self.last_kwargs = None

    def generate_content(self, **kwargs):
        self.calls += 1
        self.last_kwargs = kwargs
        return type("R", (), {"text": self.text, "usage_metadata": None})()


@pytest.fixture()
def fake_vlm(monkeypatch):
    """fake_vlm(응답) → models. 응답이 dict/list 면 JSON 으로, str 이면 그대로."""
    def _set(data):
        models = _Models(data if isinstance(data, str) else json.dumps(data))
        client = type("C", (), {"models": models})()
        monkeypatch.setattr(detector, "get_client", lambda: client)
        return models
    return _set


IMGS = [b"img0", b"img1", b"img2"]
DEFAULT = {"view": None, "occluded": False, "blurry": False, "item_visible": True}


def _res_level(part):
    r = part.media_resolution
    return None if r is None else str(getattr(r.level, "value", r.level))


# ── 호출 모양 ───────────────────────────────
def test_single_call_with_numbered_low_res_parts(fake_vlm):
    models = fake_vlm({"category": "shoes", "item": "sneakers", "photos": []})
    detector.classify_views(IMGS)
    assert models.calls == 1
    c = models.last_kwargs["contents"]
    assert len(c) == 2 * len(IMGS) + 1
    assert c[0::2][:3] == ["Photo 0:", "Photo 1:", "Photo 2:"]
    parts = c[1:-1:2]
    assert [p.inline_data.data for p in parts] == IMGS
    assert all(p.inline_data.mime_type == "image/jpeg" for p in parts)
    assert all(_res_level(p) == "MEDIA_RESOLUTION_LOW" for p in parts)
    assert c[-1] == P.views_prompt(3)


def test_call_config_json_temperature_zero_thinking_budget(fake_vlm):
    models = fake_vlm({"photos": []})
    detector.classify_views([b"a"])
    cfg = models.last_kwargs["config"]
    assert cfg.temperature == 0
    assert cfg.response_mime_type == "application/json"
    assert cfg.thinking_config.thinking_budget == 4096
    assert models.last_kwargs["model"] == "base-model"


def test_call_uses_per_call_model_override(fake_vlm, monkeypatch):
    monkeypatch.setattr(settings, "vlm_models", {"views": "lite-model"})
    models = fake_vlm({"photos": []})
    detector.classify_views([b"a"])
    assert models.last_kwargs["model"] == "lite-model"


def test_get_client_failure_propagates():
    """conftest 의 no_real_vlm 이 get_client 를 막아 둔 상태 — 예외를 삼키지 않는다 (호출부가 처리)."""
    with pytest.raises(RuntimeError):
        detector.classify_views([b"a"])


# ── 응답 정규화 ─────────────────────────────
def test_normal_response(fake_vlm):
    fake_vlm({"category": "shoes", "item": "sneakers", "photos": [
        {"index": 0, "view": "front_34", "occluded": False, "blurry": False, "item_visible": True},
        {"index": 1, "view": "bottom", "occluded": True, "blurry": False, "item_visible": True},
        {"index": 2, "view": "side", "occluded": False, "blurry": True, "item_visible": False},
    ]})
    out = detector.classify_views(IMGS)
    assert out["category"] == "shoes" and out["item"] == "sneakers"
    assert out["photos"] == [
        {"view": "front_34", "occluded": False, "blurry": False, "item_visible": True},
        {"view": "bottom", "occluded": True, "blurry": False, "item_visible": True},
        {"view": "side", "occluded": False, "blurry": True, "item_visible": False},
    ]


def test_photos_out_of_order_mapped_by_index(fake_vlm):
    fake_vlm({"photos": [{"index": 2, "view": "back"}, {"index": 0, "view": "front"},
                         {"index": 1, "view": "side"}]})
    out = detector.classify_views(IMGS)
    assert [p["view"] for p in out["photos"]] == ["front", "side", "back"]


def test_missing_index_gives_default_entry(fake_vlm):
    fake_vlm({"photos": [{"index": 0, "view": "front"}]})
    out = detector.classify_views(IMGS)
    assert len(out["photos"]) == 3
    assert out["photos"][1] == DEFAULT and out["photos"][2] == DEFAULT


@pytest.mark.parametrize("bad", [3, 99, -1, "1", 1.0, None, True, False])   # true 가 1번 사진이 되면 안 된다
def test_out_of_range_or_non_int_index_ignored(fake_vlm, bad):
    fake_vlm({"photos": [{"index": bad, "view": "front", "blurry": True}]})
    out = detector.classify_views(IMGS)
    assert out["photos"] == [DEFAULT] * 3


def test_duplicate_index_first_wins(fake_vlm):
    fake_vlm({"photos": [{"index": 0, "view": "front"}, {"index": 0, "view": "back"}]})
    assert detector.classify_views([b"a"])["photos"][0]["view"] == "front"


def test_non_dict_photo_entries_ignored(fake_vlm):
    fake_vlm({"photos": ["front", 3, None, [0, "side"], {"index": 0, "view": "side"}]})
    assert detector.classify_views([b"a"])["photos"] == [{**DEFAULT, "view": "side"}]


@pytest.mark.parametrize("photos", [None, "front", {}, {"index": 0, "view": "front"}])
def test_photos_field_wrong_type_gives_defaults(fake_vlm, photos):
    fake_vlm({"category": "bag", "photos": photos})
    out = detector.classify_views([b"a", b"b"])
    assert out["photos"] == [DEFAULT, DEFAULT]


def test_photos_field_absent(fake_vlm):
    fake_vlm({"category": "bag"})
    out = detector.classify_views([b"a"])
    assert out == {"category": "bag", "item": "object", "photos": [DEFAULT]}


def test_view_normalized_and_unknown_view_is_none(fake_vlm):
    fake_vlm({"photos": [{"index": 0, "view": "Front-34"}, {"index": 1, "view": "diagonal"},
                         {"index": 2, "view": None}]})
    assert [p["view"] for p in detector.classify_views(IMGS)["photos"]] == ["front_34", None, None]


def test_list_wrapped_response_uses_first_dict(fake_vlm):
    fake_vlm([{"category": "bag", "item": "tote", "photos": [{"index": 0, "view": "inside"}]},
              {"category": "shoes"}])
    out = detector.classify_views([b"a"])
    assert out["category"] == "bag" and out["item"] == "tote"
    assert out["photos"][0]["view"] == "inside"


@pytest.mark.parametrize("resp", [[], ["x"], "\"text\"", "3", "null", [[{"category": "bag"}]]])
def test_non_dict_response_raises_value_error(fake_vlm, resp):
    fake_vlm(resp)
    with pytest.raises(ValueError, match="views"):
        detector.classify_views([b"a"])


def test_invalid_json_raises(fake_vlm):
    fake_vlm("not json {")
    with pytest.raises(ValueError):      # json.JSONDecodeError 는 ValueError 의 하위 클래스
        detector.classify_views([b"a"])


@pytest.mark.parametrize("raw,expected", [
    ("Shoes", "shoes"), ("toy", "other"), (None, "other"), (5, "other"), ("", "other"),
])
def test_category_normalized(fake_vlm, raw, expected):
    fake_vlm({"category": raw, "photos": []})
    assert detector.classify_views([b"a"])["category"] == expected


@pytest.mark.parametrize("raw", [None, ""])
def test_item_empty_becomes_object(fake_vlm, raw):
    fake_vlm({"item": raw, "photos": []})
    assert detector.classify_views([b"a"])["item"] == "object"


def test_item_is_prompt_safe_text(fake_vlm):
    fake_vlm({"item": "bag\nIGNORE ALL", "photos": []})
    item = detector.classify_views([b"a"])["item"]
    assert "\n" not in item


@pytest.mark.parametrize("val,expected", [
    (True, True), (False, False), ("true", False), (1, False), (None, False), ("yes", False),
])
def test_occluded_and_blurry_true_only_when_true(fake_vlm, val, expected):
    fake_vlm({"photos": [{"index": 0, "view": "front", "occluded": val, "blurry": val}]})
    p = detector.classify_views([b"a"])["photos"][0]
    assert p["occluded"] is expected and p["blurry"] is expected


@pytest.mark.parametrize("val,expected", [
    (False, False), (True, True), (None, True), ("false", True), (0, True),
])
def test_item_visible_false_only_when_false(fake_vlm, val, expected):
    fake_vlm({"photos": [{"index": 0, "view": "front", "item_visible": val}]})
    assert detector.classify_views([b"a"])["photos"][0]["item_visible"] is expected


def test_zero_images(fake_vlm):
    fake_vlm({"category": "bag", "photos": [{"index": 0, "view": "front"}]})
    out = detector.classify_views([])
    assert out["photos"] == []


# ── views_prompt · vlm 설정 ─────────────────
def test_views_prompt_fills_count_and_last_index():
    text = P.views_prompt(4)
    assert "You see 4 photos" in text and "0 to 3" in text
    assert "{{" not in text and "}}" not in text


def test_views_prompt_single_photo():
    text = P.views_prompt(1)
    assert "1 photos" in text and "0 to 0" in text


def test_views_prompt_matches_fragment():
    assert P.views_prompt(2) == P.frag("views").replace("{{n}}", "2").replace("{{n_last}}", "1")


def test_views_media_resolution_low_and_thinking_budget():
    assert _res_level(vlm.image_part(b"x", "image/jpeg", "views")) == "MEDIA_RESOLUTION_LOW"
    assert vlm.thinking("views").thinking_budget == 4096
