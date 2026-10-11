"""eval/run_posts.py — gt_pieces (정답 objects.json → 세트 구성품, 10-10)."""
import json

import pytest

from conftest import _load


@pytest.fixture
def mod(tmp_path, monkeypatch):
    m = _load("run_posts")
    monkeypatch.setattr(m, "HERE", tmp_path)
    return m


def _write(tmp_path, objects):
    d = tmp_path / "data" / "posts" / "cal"
    d.mkdir(parents=True)
    (d / "objects.json").write_text(json.dumps({"objects": objects}), encoding="utf-8")


def test_gt_pieces_is_rep_only_from_used_sale_photos(tmp_path, mod):
    _write(tmp_path, [
        {"what": "desk calendar", "for_sale": True, "role": "main", "photos": ["a.jpg"]},
        {"what": "photo card", "for_sale": True, "role": "component", "count": 3, "photos": ["a.jpg", "b.jpg"]},
        {"what": "sticker", "for_sale": True, "role": "component", "photos": ["proof.jpg"]},
        {"what": "charger", "for_sale": True, "role": "accessory", "photos": ["a.jpg"]},
        {"what": "desk", "for_sale": False, "role": None, "photos": ["a.jpg"]},
    ])
    assert mod.gt_pieces("cal", ["a.jpg", "b.jpg"]) == [("main piece (desk calendar)", 1)]   # 10-10: 대표 하나만, 구성품은 상세로


def test_gt_pieces_missing_file_is_none(mod):
    assert mod.gt_pieces("nope", ["a.jpg"]) is None
