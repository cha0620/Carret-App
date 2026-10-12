"""app.services.ai.ref_matcher — 정답 구도 추천 (CLIP 은 가짜로)."""
import numpy as np
import pytest

from app.core.config import settings
from app.services.ai import ref_matcher


@pytest.fixture(autouse=True)
def on(monkeypatch):
    monkeypatch.setattr(settings, "ref_suggest", True)
    monkeypatch.setattr(settings, "pipeline_mode", "real")
    monkeypatch.setattr(settings, "ref_suggest_min", 0.5)
    monkeypatch.setattr(settings, "ref_suggest_gap", 0.06)
    ref_matcher._refs.clear()
    ref_matcher._photo_cache.clear()


@pytest.mark.parametrize("scores,expected", [
    ([("a", 0.80), ("b", 0.60), ("c", 0.55)], ["a"]),                    # 1등만 크게 앞섬
    ([("a", 0.70), ("b", 0.66), ("c", 0.65), ("d", 0.64)], ["a", "b", "c"]),   # 셋 다 붙어 있음 — 최대 3
    ([("b", 0.66), ("a", 0.70), ("c", 0.60)], ["a", "b"]),                # 순서 상관없이 정렬
    ([("a", 0.49), ("b", 0.48)], []),                                     # 1등이 낮으면 없음
    ([("a", 0.53), ("b", 0.49)], ["a"]),                                  # 차이 안이어도 MIN 미만은 뺌
    ([], []),
])
def test_pick_rule(scores, expected):
    assert [r["file"] for r in ref_matcher.pick(scores)] == expected


def _fake(monkeypatch, refs, vec_of):
    monkeypatch.setattr(ref_matcher.style_refs, "load", lambda: [{"file": f} for f in refs])
    monkeypatch.setattr(ref_matcher.style_refs, "answer_photo", lambda f: f.encode())
    calls = []

    def embed(images):
        calls.append(len(images))
        return np.array([vec_of[b.decode()] for b in images], dtype=float)
    monkeypatch.setattr(ref_matcher, "_embed", embed)
    return calls


def test_suggest_ranks_and_caches(monkeypatch):
    calls = _fake(monkeypatch, ["coat", "shoe"],
                  {"coat": [1, 0], "shoe": [0, 1], "photo": [0.8, 0.6]})
    out = ref_matcher.suggest("f1", b"photo")
    assert out == [{"file": "coat", "score": 0.8}]
    assert ref_matcher.suggest("f1", b"photo") == out
    assert calls == [2, 1]                                    # 정답 벡터 한 번 · 같은 사진은 다시 안 함


@pytest.mark.parametrize("attr,value", [("ref_suggest", False), ("pipeline_mode", "mock")])
def test_suggest_off(monkeypatch, attr, value):
    _fake(monkeypatch, ["coat"], {"coat": [1, 0], "photo": [1, 0]})
    monkeypatch.setattr(settings, attr, value)
    assert ref_matcher.suggest("f1", b"photo") == []


def test_suggest_failure_is_empty(monkeypatch):
    monkeypatch.setattr(ref_matcher.style_refs, "load", lambda: [{"file": "coat"}])
    monkeypatch.setattr(ref_matcher.style_refs, "answer_photo", lambda f: b"x")
    monkeypatch.setattr(ref_matcher, "_embed", lambda images: (_ for _ in ()).throw(RuntimeError("no model")))
    assert ref_matcher.suggest("f1", b"photo") == []
