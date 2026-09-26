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


@pytest.fixture(autouse=True)
def clear_orig_cache():
    """원본 임베딩 LRU 가 테스트 사이에 새지 않게 앞뒤로 비운다."""
    embedder_mod._ORIG_CACHE.clear()
    yield
    embedder_mod._ORIG_CACHE.clear()


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


# ── _embed_original(): 원본 임베딩 LRU 캐시 ────────────────
def _counting_embed():
    """바이트마다 결정적인 단위벡터를 돌려주고 호출 인자를 기록하는 가짜 embed."""
    calls = []
    def _embed(image_bytes):
        calls.append(image_bytes)
        v = np.array([float(len(image_bytes)), 1.0])
        return v / np.linalg.norm(v)
    return _embed, calls


def test_same_original_twice_embeds_original_once_results_every_time(monkeypatch):
    fake_embed, calls = _counting_embed()
    monkeypatch.setattr(embedder_mod, "embed", fake_embed)

    s1 = cosine_similarity(b"orig", b"res-1")
    s2 = cosine_similarity(b"orig", b"res-1")

    assert calls == [b"orig", b"res-1", b"res-1"]
    assert s1 == pytest.approx(s2)


def test_cached_original_value_is_used_in_similarity(monkeypatch):
    """캐시 적중 때도 유사도 값이 캐시 없이 계산한 값과 같다."""
    fake_embed, _ = _counting_embed()
    monkeypatch.setattr(embedder_mod, "embed", fake_embed)
    expected = float(np.dot(fake_embed(b"orig"), fake_embed(b"xx")))

    cosine_similarity(b"orig", b"a")            # 캐시 채움
    assert cosine_similarity(b"orig", b"xx") == pytest.approx(expected)


def test_different_original_is_a_miss(monkeypatch):
    fake_embed, calls = _counting_embed()
    monkeypatch.setattr(embedder_mod, "embed", fake_embed)

    cosine_similarity(b"orig-A", b"r")
    cosine_similarity(b"orig-B", b"r")

    assert calls == [b"orig-A", b"r", b"orig-B", b"r"]
    assert len(embedder_mod._ORIG_CACHE) == 2


def test_key_is_content_hash_not_object_identity(monkeypatch):
    """내용이 같은 다른 bytes 객체도 적중한다 (sha1 키)."""
    fake_embed, calls = _counting_embed()
    monkeypatch.setattr(embedder_mod, "embed", fake_embed)

    embedder_mod._embed_original(bytes(bytearray(b"same")))
    embedder_mod._embed_original(b"sa" + b"me")

    assert calls == [b"same"]


def test_eviction_past_max_drops_least_recently_used(monkeypatch):
    fake_embed, calls = _counting_embed()
    monkeypatch.setattr(embedder_mod, "embed", fake_embed)
    n = embedder_mod._ORIG_CACHE_MAX
    assert n == 16

    for i in range(n):
        embedder_mod._embed_original(b"img%d" % i)
    embedder_mod._embed_original(b"img0")          # img0 를 최근 사용으로 → img1 이 가장 오래됨
    assert len(calls) == n                          # 적중이라 호출 없음

    embedder_mod._embed_original(b"img-new")        # 17번째 → img1 축출
    assert len(embedder_mod._ORIG_CACHE) == n

    calls.clear()
    embedder_mod._embed_original(b"img0")           # 남아 있음
    assert calls == []
    embedder_mod._embed_original(b"img1")           # 축출됐으니 다시 계산
    assert calls == [b"img1"]
    assert len(embedder_mod._ORIG_CACHE) == n


def test_swapping_embed_function_is_a_miss(monkeypatch):
    """키에 embed 함수가 들어가므로, 가짜를 바꾸면 이전 가짜의 값이 새지 않는다."""
    embed_a, calls_a = _counting_embed()
    monkeypatch.setattr(embedder_mod, "embed", embed_a)
    va = embedder_mod._embed_original(b"orig")

    const = np.array([0.0, 1.0])
    calls_b = []
    def embed_b(image_bytes):
        calls_b.append(image_bytes)
        return const
    monkeypatch.setattr(embedder_mod, "embed", embed_b)
    vb = embedder_mod._embed_original(b"orig")

    assert calls_a == [b"orig"] and calls_b == [b"orig"]
    assert np.array_equal(vb, const) and not np.array_equal(va, vb)
    assert len(embedder_mod._ORIG_CACHE) == 2


def test_embed_exception_is_not_cached(monkeypatch):
    """embed 가 실패하면 캐시에 아무것도 남지 않고, 다음 호출이 다시 시도한다."""
    attempts = []
    def flaky(image_bytes):
        attempts.append(image_bytes)
        if len(attempts) == 1:
            raise RuntimeError("model load failed")
        return np.array([1.0, 0.0])
    monkeypatch.setattr(embedder_mod, "embed", flaky)

    with pytest.raises(RuntimeError):
        embedder_mod._embed_original(b"orig")
    assert len(embedder_mod._ORIG_CACHE) == 0
    assert np.array_equal(embedder_mod._embed_original(b"orig"), np.array([1.0, 0.0]))
    assert len(attempts) == 2


def test_cached_original_vector_is_read_only(monkeypatch):
    """공유 캐시 값은 읽기 전용 — 호출부의 제자리 연산이 다음 적중을 오염시키지 못한다."""
    fake_embed, _ = _counting_embed()
    monkeypatch.setattr(embedder_mod, "embed", fake_embed)

    v = embedder_mod._embed_original(b"orig")
    assert v.flags.writeable is False
    with pytest.raises(ValueError):
        v *= 2
    again = embedder_mod._embed_original(b"orig")
    assert again is v and np.allclose(again, fake_embed(b"orig"))


def test_result_embedding_is_not_frozen(monkeypatch):
    """결과 쪽 embed 는 캐시하지 않으므로 읽기 전용으로 바꾸지도 않는다."""
    produced = []
    def _embed(image_bytes):
        v = np.array([1.0, 0.0])
        produced.append((image_bytes, v))
        return v
    monkeypatch.setattr(embedder_mod, "embed", _embed)

    cosine_similarity(b"orig", b"res")

    flags = {b: v.flags.writeable for b, v in produced}
    assert flags == {b"orig": False, b"res": True}


def test_value_without_setflags_is_cached_as_is(monkeypatch):
    """setflags 가 없는 값(리스트 등 가짜)도 오류 없이 캐시된다."""
    calls = []
    def _embed(image_bytes):
        calls.append(image_bytes)
        return [1.0, 0.0]
    monkeypatch.setattr(embedder_mod, "embed", _embed)

    a = embedder_mod._embed_original(b"orig")
    b = embedder_mod._embed_original(b"orig")
    assert a == [1.0, 0.0] and a is b and calls == [b"orig"]
