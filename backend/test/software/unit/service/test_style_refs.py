"""services/style_refs.plan (10-05) — 정답 목록은 tmp json, VLM 대조는 가짜 check."""
import json

import pytest

from app.core.config import settings
from app.services import style_refs


@pytest.fixture
def refs(tmp_path, monkeypatch):
    entries = [
        {"file": "two.jpg", "category": "toy", "view": ["front", "back"], "prep": []},   # 두 면 → 제외
        {"file": "boxed.jpg", "category": "toy", "view": ["front"], "prep": ["box"]},
    ]
    p = tmp_path / "style_refs.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(settings, "style_ref_file", str(p))


PHOTOS = [{"file_id": "f1", "view": "front"}, {"file_id": "f2", "view": "front"}, {"file_id": "f3", "view": "back"}]


def _plan(check):
    return style_refs.plan("lego", "toy", PHOTOS, lambda fid: fid.encode(), check)


def test_plan_picks_photo_showing_prep(refs):
    # f1 엔 상자 없음, f2 엔 있음. 두 면 정답(two.jpg)은 prep 이 없어도 안 고른다
    assert _plan(lambda img, things: [] if img == b"f2" else list(things)) == ("boxed.jpg", "f2")


def test_plan_none_when_no_photo_has_prep(refs):
    assert _plan(lambda img, things: list(things)) is None


def test_plan_check_error_means_not_picked(refs):
    def boom(img, things):
        raise ValueError("bad")
    assert _plan(boom) is None


def test_plan_text_name_hit_with_wrong_views_does_not_fall_to_other_item(monkeypatch):
    """10-11: 폰 정답(앞 · 뒤 두 면)을 못 쓰면 같은 전자기기의 헤드폰 정답으로 넘어가지 않는다."""
    from app.services import style_refs
    monkeypatch.setattr(style_refs, "load", lambda: [
        {"file": "head.jpg", "items": ["headphone"], "category": "electronics", "view": ["front_34"], "layout": "H"},
        {"file": "phone.jpg", "items": ["phone"], "category": "electronics", "view": ["back", "front"], "layout": "P"}])
    assert style_refs.plan_text("smartphone", "electronics", [None]) is None
    assert style_refs.plan_text("speaker", "electronics", [None]) == ("head.jpg", "H")   # 이름이 맞는 게 없으면 종류로
