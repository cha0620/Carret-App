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
    assert n == 32   # 변환 1회에 원본 2장(전체·누끼)

    for i in range(n):
        embedder_mod._embed_original(b"img%d" % i)
    embedder_mod._embed_original(b"img0")          # img0 를 최근 사용으로 → img1 이 가장 오래됨
    assert len(calls) == n                          # 적중이라 호출 없음

    embedder_mod._embed_original(b"img-new")        # n+1번째 → img1 축출
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


# ══ patch_similarity: 누끼 쌍 패치 단위 국소 유사도 ══════════
# 실제 DINO 는 절대 안 부른다 — _patch_tokens 를 작은 합성 토큰 격자로, _align 은 항등으로 바꾼다.
import io  # noqa: E402
import types  # noqa: E402

from PIL import Image  # noqa: E402

BG = (128, 128, 128)
P = embedder_mod.PATCH


def _iso_png(rows, cols, item=None, bg=BG, color=(200, 30, 30), extra=None):
    """rows×cols 패치 격자 크기의 누끼 이미지. item=(r1, c1, r2, c2) 패치 범위(끝 제외)를
    물건 색으로 칠한다 (None 이면 전체). extra: [(x, y, rgb)] 픽셀 하나씩 덮어쓰기."""
    img = Image.new("RGB", (cols * P, rows * P), bg)
    r1, c1, r2, c2 = item if item is not None else (0, 0, rows, cols)
    if r2 > r1 and c2 > c1:
        img.paste(color, (c1 * P, r1 * P, c2 * P, r2 * P))
    for x, y, rgb in extra or []:
        img.putpixel((x, y), rgb)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _onehot_grid(rows, cols, extra_dims=1):
    """패치마다 서로 직교하는 단위 토큰 (rows, cols, rows*cols+extra_dims)."""
    tok = np.zeros((rows, cols, rows * cols + extra_dims))
    for r in range(rows):
        for c in range(cols):
            tok[r, c, r * cols + c] = 1.0
    return tok


def _set(tok, r, c, parts):
    """tok[r, c] 를 {차원: 값} 으로 덮어쓴다."""
    tok[r, c] = 0
    for d, v in parts.items():
        tok[r, c, d] = v


def _stub_tokens(monkeypatch, ta, tb, align=True):
    """_patch_tokens 첫 호출(원본)엔 ta, 둘째(결과)엔 tb. 받은 이미지 크기를 기록한다."""
    seen = []
    it = iter([ta, tb])

    def fake(img):
        seen.append(img.size)
        return next(it)
    monkeypatch.setattr(embedder_mod, "_patch_tokens", fake)
    if align:
        monkeypatch.setattr(embedder_mod, "_align", lambda a, b, bg: b)
    return seen


def test_patch_constants():
    assert embedder_mod.PATCH == 14
    assert embedder_mod.PATCH_PERCENTILE == 1
    assert not hasattr(embedder_mod, "PATCH_FG_MIN")


def test_patch_similarity_identical_tokens_is_one(monkeypatch):
    t = _onehot_grid(5, 5)
    _stub_tokens(monkeypatch, t, t.copy())
    assert embedder_mod.patch_similarity(_iso_png(5, 5), _iso_png(5, 5)) == pytest.approx(1.0)


def test_patch_similarity_interior_change_is_detected(monkeypatch):
    """물건 안쪽 패치 하나가 완전히 달라지면(직교) 하위 1% 가 0 쪽으로 끌려간다."""
    ta = _onehot_grid(5, 5)
    tb = ta.copy()
    _set(tb, 2, 2, {-1: 1.0})
    _stub_tokens(monkeypatch, ta, tb)
    # 5×5 전부 물건 → 침식 뒤 안쪽 3×3 = 9 패치, 그중 하나가 0
    sim = embedder_mod.patch_similarity(_iso_png(5, 5), _iso_png(5, 5))
    assert sim == pytest.approx(np.percentile([0.0] + [1.0] * 8, 1))
    assert sim < 0.1


def test_patch_similarity_edge_patch_change_is_ignored_by_erosion(monkeypatch):
    """가장자리(이웃에 격자 밖·배경이 있는) 패치 변화는 비교에서 빠진다 — 누끼 윤곽 차이."""
    ta = _onehot_grid(5, 5)
    tb = ta.copy()
    for r, c in [(0, 0), (0, 2), (4, 4), (2, 0)]:
        _set(tb, r, c, {-1: 1.0})
    _stub_tokens(monkeypatch, ta, tb)
    assert embedder_mod.patch_similarity(_iso_png(5, 5), _iso_png(5, 5)) == pytest.approx(1.0)


def test_patch_similarity_fg_requires_full_patch_and_full_neighbours(monkeypatch):
    """배경색 픽셀 하나가 섞인 패치는 'full' 이 아니고, 그 8 이웃도 안쪽에서 빠진다."""
    ta = _onehot_grid(7, 7)
    tb = ta.copy()
    _set(tb, 2, 2, {-1: 1.0})       # 바뀐 (2,2) — 하지만 이웃 (1,1) 에 배경 픽셀
    orig = _iso_png(7, 7, extra=[(1 * P + 3, 1 * P + 3, BG)])
    _stub_tokens(monkeypatch, ta, tb)
    assert embedder_mod.patch_similarity(orig, _iso_png(7, 7)) == pytest.approx(1.0)


def test_patch_similarity_one_bg_pixel_far_away_keeps_interior(monkeypatch):
    """대조군: 배경 픽셀이 (2,2) 의 이웃이 아닌 곳이면 (2,2) 변화가 잡힌다."""
    ta = _onehot_grid(7, 7)
    tb = ta.copy()
    _set(tb, 2, 2, {-1: 1.0})
    orig = _iso_png(7, 7, extra=[(5 * P + 3, 5 * P + 3, BG)])
    _stub_tokens(monkeypatch, ta, tb)
    assert embedder_mod.patch_similarity(orig, _iso_png(7, 7)) < 0.5


def test_patch_similarity_fg_is_decided_on_original_not_result(monkeypatch):
    """결과 쪽이 배경이어도 원본 물건 안쪽이면 비교한다 (물건이 사라진 걸 잡아야 한다)."""
    ta = _onehot_grid(5, 5)
    tb = np.zeros_like(ta)
    tb[..., -1] = 1.0
    _stub_tokens(monkeypatch, ta, tb)
    sim = embedder_mod.patch_similarity(_iso_png(5, 5), _iso_png(5, 5, item=(0, 0, 0, 0)))
    assert sim == pytest.approx(0.0)


@pytest.mark.parametrize("item", [
    (0, 0, 0, 0),        # 물건 없음 (전부 배경)
    (1, 1, 3, 3),        # 2×2 물건 — 침식하면 안쪽 패치가 없다
    (0, 0, 5, 1),        # 한 줄짜리
])
def test_patch_similarity_no_interior_patch_raises_value_error(monkeypatch, item):
    t = _onehot_grid(5, 5)
    _stub_tokens(monkeypatch, t, t.copy())
    with pytest.raises(ValueError, match="물건 패치가 없음"):
        embedder_mod.patch_similarity(_iso_png(5, 5, item=item), _iso_png(5, 5))


def test_patch_similarity_every_patch_has_a_bg_pixel_raises(monkeypatch):
    """물건이 꽉 차 보여도 모든 패치에 배경색 픽셀이 하나씩 있으면 'full' 패치가 없다."""
    extra = [(c * P, r * P, BG) for r in range(5) for c in range(5)]
    t = _onehot_grid(5, 5)
    _stub_tokens(monkeypatch, t, t.copy())
    with pytest.raises(ValueError):
        embedder_mod.patch_similarity(_iso_png(5, 5, extra=extra), _iso_png(5, 5))


def test_patch_similarity_single_interior_patch_is_enough(monkeypatch):
    """3×3 물건 → 안쪽 정확히 1 패치 → 그 값이 그대로."""
    ta = _onehot_grid(5, 5)
    tb = ta.copy()
    _set(tb, 2, 2, {2 * 5 + 2: 0.6, -1: 0.8})
    _stub_tokens(monkeypatch, ta, tb)
    sim = embedder_mod.patch_similarity(_iso_png(5, 5, item=(1, 1, 4, 4)), _iso_png(5, 5))
    assert sim == pytest.approx(0.6)


def test_patch_similarity_3x3_neighbourhood_absorbs_one_patch_shift(monkeypatch):
    """결과가 한 패치만큼 밀려 있어도 주변 3×3 최댓값이라 안쪽 패치는 전부 1."""
    ta = _onehot_grid(6, 6)
    tb = np.zeros_like(ta)
    tb[1:, :] = ta[:-1, :]           # 아래로 한 칸
    tb[0, :, -1] = 1.0
    _stub_tokens(monkeypatch, ta, tb)
    assert embedder_mod.patch_similarity(_iso_png(6, 6), _iso_png(6, 6)) == pytest.approx(1.0)


def test_patch_similarity_diagonal_one_patch_shift_absorbed(monkeypatch):
    ta = _onehot_grid(6, 6)
    tb = np.zeros_like(ta)
    tb[:-1, :-1] = ta[1:, 1:]        # 위·왼쪽으로 한 칸씩
    tb[-1, :, -1] = 1.0
    tb[:, -1, -1] = 1.0
    _stub_tokens(monkeypatch, ta, tb)
    assert embedder_mod.patch_similarity(_iso_png(6, 6), _iso_png(6, 6)) == pytest.approx(1.0)


def test_patch_similarity_two_patch_shift_is_not_absorbed(monkeypatch):
    ta = _onehot_grid(6, 6)
    tb = np.zeros_like(ta)
    tb[2:, :] = ta[:-2, :]           # 두 칸 — 3×3 밖
    tb[:2, :, -1] = 1.0
    _stub_tokens(monkeypatch, ta, tb)
    assert embedder_mod.patch_similarity(_iso_png(6, 6), _iso_png(6, 6)) == pytest.approx(0.0)


def test_patch_similarity_neighbourhood_takes_max_not_same_position(monkeypatch):
    """(2,2) 는 제자리 0.2, 이웃 (2,3) 이 0.9 로 닮음 → 0.9. 최솟값(PERCENTILE=0)으로 확인:
    제자리만 봤다면 0.2 가 나왔을 것."""
    ta = _onehot_grid(5, 5, extra_dims=2)
    tb = ta.copy()
    i22, i23 = 2 * 5 + 2, 2 * 5 + 3
    _set(tb, 2, 2, {i22: 0.2, -1: np.sqrt(1 - 0.04)})
    _set(tb, 2, 3, {i22: 0.9, i23: np.sqrt(1 - 0.81)})
    monkeypatch.setattr(embedder_mod, "PATCH_PERCENTILE", 0)
    _stub_tokens(monkeypatch, ta, tb)
    sim = embedder_mod.patch_similarity(_iso_png(5, 5), _iso_png(5, 5))
    # 안쪽 값: (2,2)=0.9, (2,3)=sqrt(0.19)≈0.436, 나머지 1 → 최솟값 0.436
    assert sim == pytest.approx(np.sqrt(0.19))


def test_patch_similarity_negative_cosine_is_kept(monkeypatch):
    """best 초기값은 -1 — 모든 후보가 음의 코사인이면 음수 그대로 (0 으로 자르지 않음)."""
    ta = np.zeros((5, 5, 2))
    ta[..., 0] = 1.0
    _stub_tokens(monkeypatch, ta, -ta)
    assert embedder_mod.patch_similarity(_iso_png(5, 5), _iso_png(5, 5)) == pytest.approx(-1.0)


def test_patch_similarity_is_low_percentile_not_min_or_mean(monkeypatch):
    rows = cols = 12                   # 안쪽 10×10 = 100 패치
    ta = _onehot_grid(rows, cols)
    tb = ta.copy()
    vals = []
    for r in range(1, rows - 1):
        for c in range(1, cols - 1):
            v = 0.3 + 0.007 * len(vals)
            vals.append(v)
            _set(tb, r, c, {r * cols + c: v, -1: np.sqrt(1 - v * v)})
    _stub_tokens(monkeypatch, ta, tb)
    sim = embedder_mod.patch_similarity(_iso_png(rows, cols), _iso_png(rows, cols))
    assert len(vals) == 100
    assert sim == pytest.approx(np.percentile(vals, 1))
    assert min(vals) < sim < np.mean(vals)


def test_patch_similarity_uses_patch_percentile_constant(monkeypatch):
    ta = _onehot_grid(5, 5)
    tb = ta.copy()
    for (r, c), v in zip([(1, 1), (1, 2), (1, 3)], [0.1, 0.2, 0.3]):
        _set(tb, r, c, {r * 5 + c: v, -1: np.sqrt(1 - v * v)})
    monkeypatch.setattr(embedder_mod, "PATCH_PERCENTILE", 20)
    _stub_tokens(monkeypatch, ta, tb)
    sim = embedder_mod.patch_similarity(_iso_png(5, 5), _iso_png(5, 5))
    assert sim == pytest.approx(np.percentile([0.1, 0.2, 0.3] + [1.0] * 6, 20))


def test_patch_similarity_custom_bg_color(monkeypatch):
    """bg 인자로 준 색만 배경 — 기본 회색(128)이 물건 색이어도 된다."""
    white = (255, 255, 255)
    t = _onehot_grid(5, 5)
    orig = _iso_png(5, 5, bg=white, color=BG)          # 물건 전체가 회색
    _stub_tokens(monkeypatch, t, t.copy())
    assert embedder_mod.patch_similarity(orig, orig, bg=white) == pytest.approx(1.0)
    _stub_tokens(monkeypatch, t, t.copy())
    with pytest.raises(ValueError):                       # 기본 bg 면 전부 배경으로 보인다
        embedder_mod.patch_similarity(orig, orig)


def test_patch_similarity_bg_like_but_not_equal_color_is_item(monkeypatch):
    """배경과 한 채널만 1 다른 색도 물건 — 정확히 같은 색만 배경."""
    t = _onehot_grid(5, 5)
    _stub_tokens(monkeypatch, t, t.copy())
    orig = _iso_png(5, 5, color=(128, 128, 129))
    assert embedder_mod.patch_similarity(orig, orig) == pytest.approx(1.0)


def _png_of(size, color=(200, 30, 30), mode="RGB"):
    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, format="PNG")
    return buf.getvalue()


def test_patch_similarity_crops_to_patch_multiple_and_resizes_result(monkeypatch):
    """원본 크기를 14 배수로 자르고, 크기가 다른 결과는 원본 크기로 맞춘 뒤 같게 자른다."""
    aligned = []
    monkeypatch.setattr(embedder_mod, "_align",
                        lambda a, b, bg: aligned.append((a.size, b.size, bg)) or b)
    t = _onehot_grid(4, 5)
    seen = _stub_tokens(monkeypatch, t, t.copy(), align=False)
    embedder_mod.patch_similarity(_png_of((5 * P + 9, 4 * P + 3)), _png_of((300, 250)),
                                  bg=(1, 2, 3))
    assert seen == [(5 * P, 4 * P), (5 * P, 4 * P)]
    assert aligned == [((5 * P, 4 * P), (5 * P, 4 * P), (1, 2, 3))]


@pytest.mark.parametrize("mode,color", [("RGBA", (200, 30, 30, 255)), ("L", 200)])
def test_patch_similarity_accepts_rgba_and_grayscale(monkeypatch, mode, color):
    b = _png_of((5 * P, 5 * P), color, mode)
    t = _onehot_grid(5, 5)
    _stub_tokens(monkeypatch, t, t.copy())
    assert embedder_mod.patch_similarity(b, b) == pytest.approx(1.0)


def test_patch_similarity_image_smaller_than_patch_raises(monkeypatch):
    """14px 보다 작으면 격자가 비어 비교할 패치가 없다 — 어디서든 예외 (가드가 None 으로 삼킨다)."""
    monkeypatch.setattr(embedder_mod, "_align", lambda a, b, bg: b)
    monkeypatch.setattr(embedder_mod, "_patch_tokens", lambda img: np.zeros((0, 0, 2)))
    with pytest.raises(Exception):
        embedder_mod.patch_similarity(_png_of((10, 10)), _png_of((10, 10)))


def test_patch_similarity_bad_bytes_raise_without_loading_model(monkeypatch):
    def no_model():
        raise AssertionError("모델을 로드하면 안 된다")
    monkeypatch.setattr(embedder_mod, "_load", no_model)
    with pytest.raises(Exception) as ei:
        embedder_mod.patch_similarity(b"not-an-image", b"x")
    assert not isinstance(ei.value, AssertionError)


def test_patch_similarity_observe_span_wraps_whole_call(monkeypatch):
    import contextlib
    events = []

    class Obs:
        def update(self, **kw):
            events.append(("update", kw))

    @contextlib.contextmanager
    def fake_observe(name, **kw):
        events.append(("enter", name, kw))
        try:
            yield Obs()
        finally:
            events.append(("exit",))
    monkeypatch.setattr(embedder_mod, "observe", fake_observe)
    t = _onehot_grid(5, 5)
    _stub_tokens(monkeypatch, t, t.copy())
    embedder_mod.patch_similarity(_iso_png(5, 5), _iso_png(5, 5))
    assert events == [
        ("enter", "item_patch_similarity",
         {"as_type": "embedding", "model": embedder_mod._MODEL_NAME}),
        ("update", {"output": {"patch_similarity": pytest.approx(1.0), "patches": 9}}),
        ("exit",)]

    # 디코딩 실패도 span 안에서 난다 (span 이 함수 전체를 감싼다)
    events.clear()
    with pytest.raises(Exception):
        embedder_mod.patch_similarity(b"bad", b"bad")
    assert [e[0] for e in events] == ["enter", "exit"]


def test_patch_similarity_tracing_disabled_obs_none_is_fine(monkeypatch):
    import contextlib

    @contextlib.contextmanager
    def fake_observe(name, **kw):
        yield None
    monkeypatch.setattr(embedder_mod, "observe", fake_observe)
    t = _onehot_grid(5, 5)
    _stub_tokens(monkeypatch, t, t.copy())
    assert embedder_mod.patch_similarity(_iso_png(5, 5), _iso_png(5, 5)) == pytest.approx(1.0)


# ── _align: ECC affine 정렬 (cv2 는 가벼워서 실제로 — findTransformECC 만 필요할 때 가짜) ──
def _img(arr):
    return Image.fromarray(np.asarray(arr).astype(np.uint8))


def _blob(h=56, w=56, dx=0, dy=0):
    import cv2
    a = np.full((h, w, 3), 128, np.uint8)
    a[14 + dy:42 + dy, 14 + dx:42 + dx] = (200, 30, 30)
    a[20 + dy:30 + dy, 20 + dx:26 + dx] = (20, 200, 20)
    return cv2.GaussianBlur(a, (7, 7), 2)


def _fake_ecc(monkeypatch, warp=None, exc=False):
    import cv2

    def fake(ga, gb, w0, *a, **k):
        if exc:
            raise cv2.error("ECC did not converge")
        return 1.0, np.asarray(warp, dtype=np.float32)
    monkeypatch.setattr(cv2, "findTransformECC", fake)


def test_align_ecc_failure_returns_b_unchanged(monkeypatch):
    _fake_ecc(monkeypatch, exc=True)
    b = _img(_blob(dx=2))
    assert embedder_mod._align(_img(_blob()), b, BG) is b


@pytest.mark.parametrize("warp", [
    [[2.0, 0, 0], [0, 2.0, 0]],        # det 4 — 크게 확대
    [[-1.0, 0, 0], [0, 1.0, 0]],       # det -1 — 뒤집힘
    [[0.0, 0, 0], [0, 0.0, 0]],        # det 0 — 퇴화
    [[0.79, 0, 0], [0, 1.0, 0]],       # 하한 밖
    [[1.25, 0, 0], [0, 1.0, 0]],       # det 정확히 1.25 — 경계(제외)
    [[1.0, 0, 6.0], [0, 1.0, 0]],      # x 이동 6 > 0.1*56=5.6
    [[1.0, 0, 0], [0, 1.0, -6.0]],     # y 이동 -6 (절댓값)
])
def test_align_rejects_implausible_warp(monkeypatch, warp):
    _fake_ecc(monkeypatch, warp)
    b = _img(_blob(dx=2))
    assert embedder_mod._align(_img(_blob()), b, BG) is b


@pytest.mark.parametrize("warp", [
    [[1.0, 0, 5.5], [0, 1.0, 0]],      # 이동 한계(5.6) 바로 안
    [[0.81, 0, 0], [0, 1.0, 0]],
    [[1.24, 0, 0], [0, 1.0, 0]],
    [[1.0, 0.1, 2.0], [-0.1, 1.0, -2.0]],
])
def test_align_accepts_small_warp(monkeypatch, warp):
    _fake_ecc(monkeypatch, warp)
    b = _img(_blob(dx=2))
    out = embedder_mod._align(_img(_blob()), b, BG)
    assert out is not b and out.size == (56, 56) and out.mode == "RGB"


def test_align_translation_limit_uses_longer_side(monkeypatch):
    """0.1 * max(w, h) — 긴 변 기준 (100 → 10)."""
    _fake_ecc(monkeypatch, [[1.0, 0, 9.0], [0, 1.0, 0]])
    a = _img(np.full((56, 100, 3), 128, np.uint8))
    b = _img(np.full((56, 100, 3), 128, np.uint8))
    assert embedder_mod._align(a, b, BG) is not b


def test_align_border_filled_with_bg(monkeypatch):
    """정렬로 드러난 가장자리는 bg 색 — 누끼 배경과 같아 물건 패치로 안 잡힌다."""
    _fake_ecc(monkeypatch, [[1.0, 0, 5.0], [0, 1.0, 0]])
    b = _img(np.full((56, 56, 3), (200, 30, 30), np.uint8))
    out = np.asarray(embedder_mod._align(_img(_blob()), b, (1, 2, 3)))
    assert (1, 2, 3) in {tuple(int(v) for v in out[28, 0]), tuple(int(v) for v in out[28, 55])}


def test_align_real_ecc_recovers_small_shift():
    """실제 cv2 ECC: 2px 밀린 결과를 원본에 맞춘다 (정렬 뒤 차이가 줄어든다)."""
    a, b = _img(_blob()), _img(_blob(dx=2, dy=1))
    out = embedder_mod._align(a, b, BG)
    before = np.abs(np.asarray(a, int) - np.asarray(b, int)).mean()
    after = np.abs(np.asarray(a, int) - np.asarray(out, int)).mean()
    assert out is not b and after < before / 2


def test_align_real_ecc_on_flat_images_does_not_raise():
    flat = _img(np.full((56, 56, 3), 128, np.uint8))
    assert embedder_mod._align(flat, flat, BG).size == (56, 56)


# ── _patch_tokens: 가짜 모델로 격자·정규화·검사 ──
class _Arr:
    """last_hidden_state 자리 — [0, 1:, :] 슬라이스와 .numpy() 만 흉내."""
    def __init__(self, a):
        self.a = a

    def __getitem__(self, k):
        return _Arr(self.a[k])

    def numpy(self):
        return self.a


def _fake_model(monkeypatch, n_tokens, dim=4, seed=0):
    hidden = np.random.default_rng(seed).normal(size=(1, n_tokens + 1, dim))   # CLS + 패치
    calls = []

    def processor(images=None, **kw):
        calls.append((images.size, kw))
        return {"pixel_values": "PV"}

    def model(**inputs):
        calls.append(("model", inputs))
        return types.SimpleNamespace(last_hidden_state=_Arr(hidden))
    monkeypatch.setattr(embedder_mod, "_load", lambda: (model, processor))
    return hidden, calls


def test_patch_tokens_grid_shape_normalized_and_cls_dropped(monkeypatch):
    hidden, calls = _fake_model(monkeypatch, n_tokens=3 * 4)
    tok = embedder_mod._patch_tokens(Image.new("RGB", (4 * P, 3 * P)))
    assert tok.shape == (3, 4, 4)
    assert np.allclose(np.linalg.norm(tok, axis=-1), 1.0)
    assert np.allclose(tok[0, 0], hidden[0, 1] / np.linalg.norm(hidden[0, 1]))   # CLS(0번) 제외
    assert np.allclose(tok[2, 3], hidden[0, -1] / np.linalg.norm(hidden[0, -1]))  # 행 우선
    assert np.allclose(tok[1, 0], hidden[0, 1 + 4] / np.linalg.norm(hidden[0, 1 + 4]))
    assert calls[0] == ((4 * P, 3 * P), {"do_resize": False, "do_center_crop": False,
                                        "return_tensors": "pt"})
    assert calls[1] == ("model", {"pixel_values": "PV"})


@pytest.mark.parametrize("n_tokens", [3 * 4 - 1, 3 * 4 + 4, 0])
def test_patch_tokens_count_mismatch_raises(monkeypatch, n_tokens):
    """전처리가 리사이즈했거나 register 토큰이 붙은 모델 — 틀린 격자로 재지 않는다."""
    _fake_model(monkeypatch, n_tokens=n_tokens)
    with pytest.raises(ValueError, match="패치 토큰"):
        embedder_mod._patch_tokens(Image.new("RGB", (4 * P, 3 * P)))


def test_patch_tokens_non_multiple_size_uses_floor_grid(monkeypatch):
    _fake_model(monkeypatch, n_tokens=2 * 3)
    tok = embedder_mod._patch_tokens(Image.new("RGB", (3 * P + 13, 2 * P + 1)))
    assert tok.shape[:2] == (2, 3)


def test_safety_net_blocks_real_models():
    """software/conftest.py: 테스트에서 진짜 DINO/EasyOCR 로드는 바로 실패한다."""
    from app.services.ai import local_ocr
    for load in (embedder_mod._load, local_ocr._load):
        with pytest.raises(RuntimeError, match="모델 로드 금지"):
            load()
