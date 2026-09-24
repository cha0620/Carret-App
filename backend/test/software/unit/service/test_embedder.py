"""app.services.ai.embedder.cosine_similarity - `observe(..., as_type="embedding")`
트레이싱 투명성.

`embed()` 는 DINOv2 모델을 실제로 로드하므로(네트워크/디스크 다운로드 가능),
`cosine_similarity()` 안에서 호출되는 `embed()` 자체를 가짜로 바꿔치기해서
모델을 절대 건드리지 않는다(비즈니스 로직 변경 없음 확인 목적).
"""
import numpy as np
import pytest

import app.core.tracing as tracing
import app.services.ai.embedder as embedder_mod
from app.services.ai.embedder import cosine_similarity


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


def _fake_embed_pair(vec_a, vec_b):
    calls = []
    def _embed(image_bytes):
        calls.append(image_bytes)
        return vec_a if len(calls) == 1 else vec_b
    return _embed, calls


def test_cosine_similarity_disabled_tracing_matches_plain_dot_product(monkeypatch):
    a = np.array([1.0, 0.0])
    b = np.array([0.0, 1.0])   # 직교 -> 코사인 유사도 0
    fake_embed, calls = _fake_embed_pair(a, b)
    monkeypatch.setattr(embedder_mod, "embed", fake_embed)

    sim = cosine_similarity(b"orig", b"result")

    assert sim == pytest.approx(0.0)
    assert calls == [b"orig", b"result"]


def test_cosine_similarity_identical_vectors_returns_one(monkeypatch):
    v = np.array([0.6, 0.8])   # 이미 단위벡터
    fake_embed, _ = _fake_embed_pair(v, v)
    monkeypatch.setattr(embedder_mod, "embed", fake_embed)

    sim = cosine_similarity(b"orig", b"result")

    assert sim == pytest.approx(1.0)


def test_cosine_similarity_enabled_tracing_invokes_obs_update(monkeypatch):
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

    a = np.array([1.0, 0.0])
    fake_embed, _ = _fake_embed_pair(a, a)
    monkeypatch.setattr(embedder_mod, "embed", fake_embed)

    sim = cosine_similarity(b"orig", b"result")

    assert sim == pytest.approx(1.0)
    assert fake_lf.calls[0]["name"] == "dino_similarity"
    assert fake_lf.calls[0]["as_type"] == "embedding"
    assert fake_lf.obs.update_calls == [{"output": {"cosine_similarity": pytest.approx(1.0)}}]
