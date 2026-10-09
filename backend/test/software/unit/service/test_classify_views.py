"""detector.classify_views · group_objects(10-04) — 여러 장을 VLM 1회로 (낮은 해상도) + 응답 정규화.

test_detector.py 처럼 detector.get_client 를 가짜 클라이언트로 바꿔치기한다 (실제 호출 없음).
views_prompt(n) · vlm 의 "views" 설정도 여기서 본다.
"""
import json

import pytest

import app.core.tracing as tracing
import app.prompts as P
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


@pytest.mark.parametrize("bad", [3, 99, -1,])   # true 가 1번 사진이 되면 안 된다
def test_out_of_range_or_non_int_index_ignored(fake_vlm, bad):
    fake_vlm({"photos": [{"index": bad, "view": "front", "blurry": True}]})
    out = detector.classify_views(IMGS)
    assert out["photos"] == [DEFAULT] * 3


@pytest.mark.parametrize("resp", [[], ["x"],])
def test_non_dict_response_raises_value_error(fake_vlm, resp):
    fake_vlm(resp)
    with pytest.raises(ValueError, match="views"):
        detector.classify_views([b"a"])


@pytest.mark.parametrize("val,expected", [
    (True, True), (False, False), ("true", False),])
def test_occluded_and_blurry_true_only_when_true(fake_vlm, val, expected):
    fake_vlm({"photos": [{"index": 0, "view": "front", "occluded": val, "blurry": val}]})
    p = detector.classify_views([b"a"])["photos"][0]
    assert p["occluded"] is expected and p["blurry"] is expected


# ── 10-04: group_objects (물건별로 묶기) ─────
OBJ_RESP = {"objects": [{"id": "A", "kind": "product", "name": "sneakers", "label": "운동화", "desc": "",
                         "category": "shoes", "for_sale": True},
                        {"id": "B", "kind": "proof", "proof_type": "document", "proof_for": "A",
                         "name": "warranty card", "label": "보증서"}],
            "photos": [{"index": 0, "object": "A", "view": "side"}, {"index": 1, "object": "B", "view": "front"},
                       {"index": 2, "object": "A", "view": "bottom", "blurry": True}]}


def test_group_objects_single_call_low_res_with_objects_prompt(fake_vlm):
    models = fake_vlm(OBJ_RESP)
    detector.group_objects(IMGS)
    assert models.calls == 1
    c = models.last_kwargs["contents"]
    assert len(c) == 2 * len(IMGS) + 1
    assert c[0::2][:3] == ["Photo 0:", "Photo 1:", "Photo 2:"]
    parts = c[1:-1:2]
    assert [p.inline_data.data for p in parts] == IMGS
    assert all(p.inline_data.mime_type == "image/jpeg" for p in parts)
    assert all(_res_level(p) == "MEDIA_RESOLUTION_LOW" for p in parts)
    assert c[-1] == P.objects_prompt(3)


def test_group_objects_returns_normalized(fake_vlm):
    from app.services import listing
    fake_vlm(OBJ_RESP)
    out = detector.group_objects(IMGS)
    assert out == listing.normalize(OBJ_RESP, 3)
    assert [o["id"] for o in out["objects"]] == ["o1", "o2"]
    assert out["objects"][1]["proof_for"] == "o1"
    assert [(p["object"], p["view"]) for p in out["photos"]] == [("o1", "side"), ("o2", None), ("o1", "bottom")]
    assert out["photos"][2]["blurry"] is True


def test_group_objects_old_shape_response_one_object(fake_vlm):
    """옛 views 모양 응답(objects 없음)이 와도 물건 하나로 — 사진 모두 o1."""
    fake_vlm({"category": "bag", "item": "tote", "photos": [{"index": 0, "view": "front"}]})
    out = detector.group_objects([b"a", b"b"])
    assert [o["name"] for o in out["objects"]] == ["tote"] and out["objects"][0]["category"] == "bag"
    assert [p["object"] for p in out["photos"]] == ["o1", "o1"]
    assert [p["view"] for p in out["photos"]] == ["front", None]       # 각도는 살린다


def test_objects_prompt_lists_all_kinds_views_categories():
    from app.services import coverage, listing
    text = P.objects_prompt(2)
    for word in [*coverage.CATEGORIES, *listing.KINDS, *listing.PROOF_TYPES]:
        assert f'"{word}"' in text, word
    assert '"view"' not in text   # 각도는 묶음에서 묻지 않는다 — AI 사진 검토가 대신 (10-09 사용자)


