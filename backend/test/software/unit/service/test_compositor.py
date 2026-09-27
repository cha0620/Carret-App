"""배경 교체 모드 합성 — 실제 rembg 없이 알파를 직접 넣어 검증."""
import io

import numpy as np
from PIL import Image

from app.services.ai import compositor


def _img(w=200, h=100, color=(200, 30, 30)):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _alpha(h=100, w=200, box=(50, 20, 150, 80)):
    a = np.zeros((h, w), np.uint8)
    x1, y1, x2, y2 = box
    a[y1:y2, x1:x2] = 255
    return a


def test_compose_centers_item_on_square_canvas_with_preset_bg():
    out = Image.open(io.BytesIO(compositor.compose(_img(), (245, 245, 245), alpha=_alpha())))
    assert out.size == (compositor.CANVAS, compositor.CANVAS)
    c = compositor.CANVAS // 2
    r, g, b = out.getpixel((c, c))
    assert r > 150 and g < 80 and b < 80            # 가운데 = 원본 물건 색
    assert out.getpixel((5, 5)) == (245, 245, 245)  # 모서리 = 프리셋 배경색


def test_compose_does_not_upscale_beyond_cap():
    out = Image.open(io.BytesIO(compositor.compose(_img(), (255, 255, 255), alpha=_alpha())))
    arr = np.asarray(out)
    red_cols = np.where((arr[:, :, 0] > 150) & (arr[:, :, 1] < 80))[1]
    assert red_cols.max() - red_cols.min() + 1 <= 100 * compositor.MAX_UPSCALE + 2


def test_clean_alpha_drops_small_blobs_and_outside_item_box():
    a = _alpha()
    a[0:5, 0:5] = 255                                # 떨어진 작은 조각
    cleaned = compositor.clean_alpha(a)
    assert cleaned[2, 2] == 0 and cleaned[50, 100] == 255
    boxed = compositor.clean_alpha(_alpha(), {"x1": 0, "y1": 0, "x2": 400, "y2": 1000})
    assert boxed[50, 60] == 255 and boxed[50, 140] == 0   # 박스(가로 0~40%+여유) 밖은 지움


def test_compose_empty_alpha_raises():
    import pytest
    with pytest.raises(ValueError):
        compositor.compose(_img(), (255, 255, 255), alpha=np.zeros((100, 200), np.uint8))


def test_cutout_prefers_fal_and_falls_back_to_local(monkeypatch):
    """conftest 가 막아 둔 cutout_alpha 대신 원래 함수를 다시 불러와 분기만 검증."""
    from app.core.config import settings
    mod = compositor
    cut = compositor._cutout_alpha_impl
    img = Image.new("RGB", (4, 4))
    monkeypatch.setattr(settings, "cutout_backend", "fal")
    monkeypatch.setattr(mod, "_fal_alpha", lambda i: np.full((4, 4), 7, np.uint8))
    monkeypatch.setattr(mod, "_local_alpha", lambda i: np.full((4, 4), 9, np.uint8))
    assert cut(img)[0, 0] == 7

    def boom(i):
        raise RuntimeError("fal down")
    monkeypatch.setattr(mod, "_fal_alpha", boom)
    assert cut(img)[0, 0] == 9

    monkeypatch.setattr(settings, "cutout_backend", "local")
    monkeypatch.setattr(mod, "_fal_alpha", lambda i: np.full((4, 4), 7, np.uint8))
    assert cut(img)[0, 0] == 9


# ══ original_alpha: 원본 알파 캐시 ══════════════════════
import threading
from collections import OrderedDict

import pytest


@pytest.fixture(autouse=True)
def fresh_alpha_cache(monkeypatch):
    """테스트끼리 캐시가 섞이지 않게 매번 빈 캐시로."""
    monkeypatch.setattr(compositor, "_ALPHA_CACHE", OrderedDict())


def _counting_cutout(monkeypatch, alpha_fn=None):
    """cutout_alpha 를 호출 기록용 가짜로 바꾼다 — (img.size, kw) 를 남긴다."""
    calls = []

    def fake(img, **kw):
        calls.append((img.size, kw))
        if alpha_fn is not None:
            return alpha_fn(img)
        return _alpha(h=img.size[1], w=img.size[0], box=(50, 20, 150, 80))
    monkeypatch.setattr(compositor, "cutout_alpha", fake)
    return calls


def _open(b):
    return Image.open(io.BytesIO(b)).convert("RGB")


def test_original_alpha_cache_hit_calls_cutout_once_and_returns_same_array(monkeypatch):
    calls = _counting_cutout(monkeypatch)
    b = _img()
    a1 = compositor.original_alpha(b, _open(b))
    a2 = compositor.original_alpha(b, _open(b))
    assert a1 is a2
    assert len(calls) == 1
    # backend 인자 없음(→ 설정), fal 실패는 올려 받는다 (대체 알파를 캐시하지 않으려고)
    assert calls[0][1] == {"fallback": False}


def test_original_alpha_different_bytes_miss(monkeypatch):
    calls = _counting_cutout(monkeypatch)
    b1, b2 = _img(color=(1, 2, 3)), _img(color=(3, 2, 1))
    compositor.original_alpha(b1, _open(b1))
    compositor.original_alpha(b2, _open(b2))
    assert len(calls) == 2


def test_original_alpha_key_includes_cutout_alpha_identity(monkeypatch):
    b = _img()
    monkeypatch.setattr(compositor, "cutout_alpha", lambda img, **kw: np.full((100, 200), 1, np.uint8))
    first = compositor.original_alpha(b, _open(b))
    monkeypatch.setattr(compositor, "cutout_alpha", lambda img, **kw: np.full((100, 200), 2, np.uint8))
    second = compositor.original_alpha(b, _open(b))
    assert first[0, 0] == 1 and second[0, 0] == 2


def test_original_alpha_is_read_only(monkeypatch):
    _counting_cutout(monkeypatch)
    b = _img()
    a = compositor.original_alpha(b, _open(b))
    assert a.flags.writeable is False
    with pytest.raises(ValueError):
        a[0, 0] = 1


def test_original_alpha_non_ndarray_result_is_cached_without_setflags(monkeypatch):
    """setflags 가 없는 반환값도 그대로 캐시 (hasattr 가드)."""
    sentinel = object()
    calls = []
    monkeypatch.setattr(compositor, "cutout_alpha", lambda img, **kw: calls.append(1) or sentinel)
    b = _img()
    assert compositor.original_alpha(b, _open(b)) is sentinel
    assert compositor.original_alpha(b, _open(b)) is sentinel
    assert calls == [1]


def test_original_alpha_exception_is_not_cached(monkeypatch):
    """backend=local: 오리기 예외는 그대로 올리고 캐시하지 않는다."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "cutout_backend", "local")
    state = {"n": 0}

    def flaky(img, **kw):
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("fal down")
        return _alpha()
    monkeypatch.setattr(compositor, "cutout_alpha", flaky)
    b = _img()
    with pytest.raises(RuntimeError):
        compositor.original_alpha(b, _open(b))
    assert compositor.original_alpha(b, _open(b))[50, 100] == 255
    assert state["n"] == 2


def test_original_alpha_lru_evicts_oldest_beyond_max(monkeypatch):
    calls = _counting_cutout(monkeypatch)
    n = compositor._ALPHA_CACHE_MAX
    assert n == 8   # 원본 해상도 알파라 작게
    imgs = [_img(color=(i, 0, 0)) for i in range(n + 1)]
    for b in imgs[:n]:
        compositor.original_alpha(b, _open(b))
    compositor.original_alpha(imgs[0], _open(imgs[0]))    # 0번을 최근으로 → 1번이 가장 오래됨
    assert len(calls) == n
    compositor.original_alpha(imgs[n], _open(imgs[n]))    # 넘침 → 1번 축출
    assert len(compositor._ALPHA_CACHE) == n
    compositor.original_alpha(imgs[0], _open(imgs[0]))    # 여전히 캐시
    assert len(calls) == n + 1
    compositor.original_alpha(imgs[1], _open(imgs[1]))    # 축출됐으니 다시 계산
    assert len(calls) == n + 2


def test_original_alpha_concurrent_calls_return_consistent_values(monkeypatch):
    _counting_cutout(monkeypatch)
    b = _img()
    out, errs = [], []

    def worker():
        try:
            out.append(compositor.original_alpha(b, _open(b)))
        except Exception as e:  # pragma: no cover
            errs.append(e)
    ts = [threading.Thread(target=worker) for _ in range(8)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert errs == [] and len(out) == 8
    assert all(np.array_equal(o, out[0]) for o in out)
    assert len(compositor._ALPHA_CACHE) == 1


def test_compose_without_alpha_uses_cached_original_alpha(monkeypatch):
    calls = _counting_cutout(monkeypatch)
    b = _img()
    first = compositor.compose(b, (245, 245, 245))
    second = compositor.compose(b, (245, 245, 245))
    assert len(calls) == 1 and first == second


def test_compose_after_isolate_reuses_original_cutout(monkeypatch):
    """상품 가드(isolate original=True)가 오린 원본을 배경 교체가 다시 오리지 않는다."""
    calls = _counting_cutout(monkeypatch)
    b = _img()
    compositor.isolate(b, original=True)
    compositor.compose(b, (245, 245, 245))
    assert len(calls) == 1


def test_compose_with_read_only_alpha_does_not_raise():
    a = _alpha()
    a.setflags(write=False)
    out = Image.open(io.BytesIO(compositor.compose(_img(), (245, 245, 245), alpha=a)))
    assert out.size == (compositor.CANVAS, compositor.CANVAS)
    assert a.flags.writeable is False and a[50, 100] == 255   # 입력은 그대로


# ══ cutout_alpha(backend=...) ════════════════════════
@pytest.mark.parametrize("setting,backend,expected", [
    ("fal", "local", 9),    # 설정이 fal 이어도 backend="local" 이면 로컬
    ("local", "fal", 7),    # 설정이 local 이어도 backend="fal" 이면 fal
    ("fal", None, 7),       # 안 주면 설정
    ("local", None, 9),
    ("fal", "", 7),         # 빈 문자열은 '안 줌' 과 같다 (or)
])
def test_cutout_backend_argument_overrides_settings(monkeypatch, setting, backend, expected):
    from app.core.config import settings
    monkeypatch.setattr(settings, "cutout_backend", setting)
    monkeypatch.setattr(compositor, "_fal_alpha", lambda i: np.full((4, 4), 7, np.uint8))
    monkeypatch.setattr(compositor, "_local_alpha", lambda i: np.full((4, 4), 9, np.uint8))
    img = Image.new("RGB", (4, 4))
    assert compositor._cutout_alpha_impl(img, backend=backend)[0, 0] == expected


def test_cutout_backend_fal_override_still_falls_back_to_local(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "cutout_backend", "local")

    def boom(i):
        raise RuntimeError("fal down")
    monkeypatch.setattr(compositor, "_fal_alpha", boom)
    monkeypatch.setattr(compositor, "_local_alpha", lambda i: np.full((4, 4), 9, np.uint8))
    assert compositor._cutout_alpha_impl(Image.new("RGB", (4, 4)), backend="fal")[0, 0] == 9


# ══ isolate: 물건만 오려 회색 배경 가운데에 ══════════════
def _red_bbox(png):
    arr = np.asarray(_open(png)).astype(int)
    ys, xs = np.nonzero((arr[:, :, 0] > 150) & (arr[:, :, 1] < 80) & (arr[:, :, 2] < 80))
    return xs.min(), ys.min(), xs.max(), ys.max()


def test_isolate_output_is_png_448_square_on_neutral_gray(monkeypatch):
    _counting_cutout(monkeypatch)
    out = compositor.isolate(_img())
    assert out[:8] == b"\x89PNG\r\n\x1a\n"
    img = _open(out)
    assert img.size == (compositor.ISOLATE_SIZE, compositor.ISOLATE_SIZE) == (448, 448)
    assert compositor.ISOLATE_BG == (128, 128, 128)
    for corner in [(0, 0), (447, 0), (0, 447), (447, 447)]:
        assert img.getpixel(corner) == (128, 128, 128)


def test_isolate_item_is_centered_and_fills_90_percent(monkeypatch):
    _counting_cutout(monkeypatch)       # 200x100 중 (50,20)-(150,80) = 100x60 물건
    x1, y1, x2, y2 = _red_bbox(compositor.isolate(_img()))
    w, h = x2 - x1 + 1, y2 - y1 + 1
    assert abs(w - round(448 * 0.9)) <= 2            # 긴 변이 90%
    assert abs(h - round(448 * 0.9 * 60 / 100)) <= 2  # 비율 유지
    assert abs((x1 + x2) / 2 - 447 / 2) <= 1.5
    assert abs((y1 + y2) / 2 - 447 / 2) <= 1.5
    assert _open(compositor.isolate(_img())).getpixel((224, 224)) == (200, 30, 30)


def test_isolate_is_position_and_scale_invariant(monkeypatch):
    """같은 물건이 원본 어디에 있든, 크기가 달라도 같은 모양의 결과."""
    boxes = {}

    def fake(img, **kw):
        return _alpha(h=img.size[1], w=img.size[0], box=boxes[img.size])
    monkeypatch.setattr(compositor, "cutout_alpha", fake)
    boxes[(200, 100)] = (10, 10, 60, 40)       # 50x30 왼쪽 위
    boxes[(400, 400)] = (250, 300, 350, 360)   # 100x60 오른쪽 아래
    a = _red_bbox(compositor.isolate(_img()))
    b = _red_bbox(compositor.isolate(_img(400, 400)))
    assert all(abs(p - q) <= 2 for p, q in zip(a, b))


def test_isolate_does_not_cap_upscale_unlike_compose(monkeypatch):
    """아주 작은 물건도 90% 까지 키운다 (compose 의 MAX_UPSCALE 은 적용 안 됨)."""
    monkeypatch.setattr(compositor, "cutout_alpha",
                        lambda img, **kw: _alpha(h=img.size[1], w=img.size[0], box=(95, 45, 105, 55)))
    x1, _, x2, _ = _red_bbox(compositor.isolate(_img()))
    assert x2 - x1 + 1 >= 400


def test_isolate_one_pixel_item_does_not_crash(monkeypatch):
    monkeypatch.setattr(compositor, "cutout_alpha",
                        lambda img, **kw: _alpha(h=img.size[1], w=img.size[0], box=(100, 50, 101, 51)))
    assert _open(compositor.isolate(_img())).size == (448, 448)


def test_isolate_extreme_aspect_ratio_keeps_min_1px(monkeypatch):
    monkeypatch.setattr(compositor, "cutout_alpha",
                        lambda img, **kw: _alpha(h=img.size[1], w=img.size[0], box=(0, 50, 2000, 51)))
    out = compositor.isolate(_img(2000, 100))
    assert _open(out).size == (448, 448)


def test_isolate_empty_alpha_raises_value_error(monkeypatch):
    monkeypatch.setattr(compositor, "cutout_alpha",
                        lambda img, **kw: np.zeros((img.size[1], img.size[0]), np.uint8))
    with pytest.raises(ValueError):
        compositor.isolate(_img())


def test_isolate_result_uses_local_backend_and_no_cache(monkeypatch):
    calls = _counting_cutout(monkeypatch)
    b = _img()
    compositor.isolate(b)
    compositor.isolate(b)
    assert [kw for _, kw in calls] == [{"backend": "local"}, {"backend": "local"}]
    assert len(compositor._ALPHA_CACHE) == 0


def test_isolate_original_uses_cache_and_settings_backend(monkeypatch):
    calls = _counting_cutout(monkeypatch)
    b = _img()
    first = compositor.isolate(b, original=True)
    second = compositor.isolate(b, original=True)
    assert first == second
    assert [kw for _, kw in calls] == [{"fallback": False}]   # backend 미지정 → settings
    assert len(compositor._ALPHA_CACHE) == 1


def test_isolate_original_really_resolves_backend_from_settings(monkeypatch):
    """실제 cutout_alpha 분기: original=True 는 설정(fal), 결과는 local."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "cutout_backend", "fal")
    used = []
    box = _alpha()
    monkeypatch.setattr(compositor, "_fal_alpha", lambda i: used.append("fal") or box)
    monkeypatch.setattr(compositor, "_local_alpha", lambda i: used.append("local") or box)
    monkeypatch.setattr(compositor, "cutout_alpha", compositor._cutout_alpha_impl)
    compositor.isolate(_img(), original=True)
    compositor.isolate(_img())
    assert used == ["fal", "local"]


def test_isolate_item_box_is_applied(monkeypatch):
    """item_box 밖은 지운다 — 박스가 물건을 벗어나면 빈 알파 → ValueError."""
    _counting_cutout(monkeypatch)
    with pytest.raises(ValueError):
        compositor.isolate(_img(), {"x1": 900, "y1": 900, "x2": 1000, "y2": 1000}, original=True)
    # 물건을 감싸는 박스면 정상
    out = compositor.isolate(_img(), {"x1": 200, "y1": 150, "x2": 800, "y2": 850}, original=True)
    assert _open(out).getpixel((224, 224)) == (200, 30, 30)


def test_isolate_accepts_rgba_and_grayscale_input(monkeypatch):
    _counting_cutout(monkeypatch)
    for mode, color in (("RGBA", (200, 30, 30, 255)), ("L", 200)):
        buf = io.BytesIO()
        Image.new(mode, (200, 100), color).save(buf, format="PNG")
        assert _open(compositor.isolate(buf.getvalue())).size == (448, 448)


def test_isolate_does_not_mutate_cached_alpha(monkeypatch):
    _counting_cutout(monkeypatch)
    b = _img()
    # 박스로 절반을 지우는 clean_alpha 를 거쳐도 캐시 원본은 그대로
    compositor.isolate(b, {"x1": 0, "y1": 0, "x2": 500, "y2": 1000}, original=True)
    cached = next(iter(compositor._ALPHA_CACHE.values()))
    assert cached[50, 100] == 255 and cached.sum() == 255 * 100 * 60


# ══ fal 실패 → 로컬 대체 알파는 캐시하지 않는다 ═══════════
def _real_cutout(monkeypatch, backend, fal_results):
    """실제 cutout_alpha 분기 + 가짜 _fal_alpha/_local_alpha. fal_results 는 호출마다
    차례로: Exception 이면 raise, 아니면 알파 값(uint8). 호출 기록 반환."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "cutout_backend", backend)
    monkeypatch.setattr(compositor, "cutout_alpha", compositor._cutout_alpha_impl)
    it = iter(fal_results)
    used = []

    def fal(img):
        used.append("fal")
        v = next(it)
        if isinstance(v, Exception):
            raise v
        return np.full((img.size[1], img.size[0]), v, np.uint8)

    def local(img):
        used.append("local")
        return np.full((img.size[1], img.size[0]), 9, np.uint8)
    monkeypatch.setattr(compositor, "_fal_alpha", fal)
    monkeypatch.setattr(compositor, "_local_alpha", local)
    return used


def test_original_alpha_fal_failure_falls_back_to_local_without_caching(monkeypatch):
    used = _real_cutout(monkeypatch, "fal", [RuntimeError("fal down"), 7])
    b = _img()
    first = compositor.original_alpha(b, _open(b))
    assert first[0, 0] == 9 and used == ["fal", "local"]
    assert len(compositor._ALPHA_CACHE) == 0
    assert first.flags.writeable is True           # 캐시 안 한 값은 잠그지 않는다
    # 다음 호출은 fal 을 다시 시도하고, 성공하면 그걸 캐시
    second = compositor.original_alpha(b, _open(b))
    assert second[0, 0] == 7 and used == ["fal", "local", "fal"]
    assert len(compositor._ALPHA_CACHE) == 1
    assert compositor.original_alpha(b, _open(b)) is second
    assert used == ["fal", "local", "fal"]


def test_original_alpha_fal_keeps_failing_calls_fal_every_time(monkeypatch):
    used = _real_cutout(monkeypatch, "fal", [RuntimeError("a"), RuntimeError("b")])
    b = _img()
    compositor.original_alpha(b, _open(b))
    compositor.original_alpha(b, _open(b))
    assert used == ["fal", "local", "fal", "local"]
    assert len(compositor._ALPHA_CACHE) == 0


def test_original_alpha_fal_and_local_both_fail_raises(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "cutout_backend", "fal")

    def boom(img, **kw):
        raise RuntimeError("down: " + str(kw))
    monkeypatch.setattr(compositor, "cutout_alpha", boom)
    b = _img()
    with pytest.raises(RuntimeError, match="'backend': 'local'"):
        compositor.original_alpha(b, _open(b))
    assert len(compositor._ALPHA_CACHE) == 0


def test_original_alpha_local_backend_failure_reraises_without_fallback(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "cutout_backend", "local")
    calls = []

    def boom(img, **kw):
        calls.append(kw)
        raise RuntimeError("rembg OOM")
    monkeypatch.setattr(compositor, "cutout_alpha", boom)
    b = _img()
    with pytest.raises(RuntimeError, match="rembg OOM"):
        compositor.original_alpha(b, _open(b))
    assert calls == [{"fallback": False}]           # 로컬로 한 번 더 시도하지 않는다


def test_original_alpha_cache_key_includes_backend_setting(monkeypatch):
    from app.core.config import settings
    calls = _counting_cutout(monkeypatch)
    b = _img()
    monkeypatch.setattr(settings, "cutout_backend", "fal")
    a_fal = compositor.original_alpha(b, _open(b))
    monkeypatch.setattr(settings, "cutout_backend", "local")
    a_local = compositor.original_alpha(b, _open(b))
    assert len(calls) == 2 and a_fal is not a_local
    assert len(compositor._ALPHA_CACHE) == 2
    monkeypatch.setattr(settings, "cutout_backend", "fal")
    assert compositor.original_alpha(b, _open(b)) is a_fal
    assert len(calls) == 2


def test_compose_after_fal_fallback_retries_fal(monkeypatch):
    """배경 교체(정직성 폴백)가 상품 가드 때의 저품질 대체 알파를 물려받지 않는다."""
    used = _real_cutout(monkeypatch, "fal", [RuntimeError("fal down"), 255])
    monkeypatch.setattr(compositor, "_local_alpha",   # 불투명 알파라야 isolate 가 물건을 찾는다
                        lambda img: used.append("local") or np.full((img.size[1], img.size[0]), 255, np.uint8))
    b = _img()
    compositor.isolate(b, original=True)            # fal 실패 → local 로 isolate
    compositor.compose(b, (245, 245, 245))          # fal 재시도
    assert used == ["fal", "local", "fal"]


# ══ cutout_alpha(fallback=False) ════════════════════════
def test_cutout_fallback_false_reraises_fal_error(monkeypatch):
    used = _real_cutout(monkeypatch, "fal", [RuntimeError("fal down")])
    with pytest.raises(RuntimeError, match="fal down"):
        compositor.cutout_alpha(Image.new("RGB", (4, 4)), fallback=False)
    assert used == ["fal"]


def test_cutout_fallback_false_local_backend_just_uses_local(monkeypatch):
    used = _real_cutout(monkeypatch, "local", [])
    assert compositor.cutout_alpha(Image.new("RGB", (4, 4)), fallback=False)[0, 0] == 9
    assert used == ["local"]


def test_cutout_fallback_false_fal_success_returns_fal(monkeypatch):
    used = _real_cutout(monkeypatch, "fal", [7])
    assert compositor.cutout_alpha(Image.new("RGB", (4, 4)), fallback=False)[0, 0] == 7
    assert used == ["fal"]


# ══ 락 분리: rembg 로드가 캐시 조회를 막지 않는다 ═══════
def test_load_lock_is_separate_from_cache_lock():
    assert isinstance(compositor._load_lock, type(threading.Lock()))
    assert compositor._load_lock is not compositor._lock


def test_cache_hit_not_blocked_while_model_load_lock_held(monkeypatch):
    _counting_cutout(monkeypatch)
    b = _img()
    compositor.original_alpha(b, _open(b))          # 캐시에 넣어 둠
    done = threading.Event()
    with compositor._load_lock:                     # 모델 로드 중인 상황
        t = threading.Thread(target=lambda: (compositor.original_alpha(b, _open(b)), done.set()))
        t.start()
        assert done.wait(2), "모델 로드 락이 캐시 조회를 막았다"
    t.join()


# ══ _fal_alpha: client_timeout ══════════════════════════
def test_fal_alpha_passes_client_timeout(monkeypatch):
    import sys
    import types
    import httpx
    from app.services.ai import generator

    seen = {}
    fake_fal = types.ModuleType("fal_client")

    def subscribe(app, arguments=None, **kw):
        seen.update(app=app, arguments=arguments, kw=kw)
        return {"image": {"url": "http://mask"}}
    fake_fal.subscribe = subscribe
    monkeypatch.setitem(sys.modules, "fal_client", fake_fal)
    monkeypatch.setattr(generator, "_upload", lambda data: "http://uploaded")
    buf = io.BytesIO()
    Image.new("L", (2, 2), 200).save(buf, format="PNG")

    class R:
        content = buf.getvalue()

        def raise_for_status(self):
            seen["raised_checked"] = True
            return self
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None, follow_redirects=False: seen.update(get_timeout=timeout) or R())

    a = compositor._fal_alpha(Image.new("RGB", (4, 3)))

    assert seen["kw"] == {"client_timeout": 60}
    assert seen["app"] == compositor.FAL_CUTOUT and seen["arguments"]["mask_only"] is True
    assert seen["get_timeout"] == 60
    assert seen["raised_checked"] is True          # 상태 코드 확인 후에 마스크를 읽는다
    assert a.shape == (3, 4)


# ══ _fal_alpha: 에러 상태 → 예외 → cutout_alpha 가 로컬로 대체 ══
def _fal_alpha_http(monkeypatch, response):
    import sys
    import types
    import httpx
    from app.services.ai import generator

    fake_fal = types.ModuleType("fal_client")
    fake_fal.subscribe = lambda app, arguments=None, **kw: {"image": {"url": "http://mask"}}
    monkeypatch.setitem(sys.modules, "fal_client", fake_fal)
    monkeypatch.setattr(generator, "_upload", lambda data: "http://uploaded")
    monkeypatch.setattr(httpx, "get", lambda url, timeout=None, follow_redirects=False: response)
    opened = []
    real_open = Image.open
    monkeypatch.setattr(compositor.Image, "open", lambda f, *a, **k: opened.append(1) or real_open(f, *a, **k))
    return opened


def _http_resp(status, content):
    import httpx
    return httpx.Response(status, content=content, request=httpx.Request("GET", "http://mask"))


@pytest.mark.parametrize("status", [500, 503, 404, 403])
def test_fal_alpha_error_status_raises_before_reading_image(monkeypatch, status):
    import httpx
    opened = _fal_alpha_http(monkeypatch, _http_resp(status, b"<html>error</html>"))
    with pytest.raises(httpx.HTTPStatusError):
        compositor._fal_alpha(Image.new("RGB", (4, 3)))
    assert opened == []          # HTML 에러 페이지를 PIL 로 열지 않는다


@pytest.mark.parametrize("status", [500, 404])
def test_cutout_alpha_fal_error_status_falls_back_to_local(monkeypatch, status):
    _fal_alpha_http(monkeypatch, _http_resp(status, b"<html>error</html>"))
    monkeypatch.setattr(compositor, "_local_alpha", lambda i: np.full((3, 4), 9, np.uint8))
    a = compositor._cutout_alpha_impl(Image.new("RGB", (4, 3)), backend="fal")
    assert a.shape == (3, 4) and a[0, 0] == 9


def test_cutout_alpha_fal_error_status_no_fallback_raises(monkeypatch):
    import httpx
    _fal_alpha_http(monkeypatch, _http_resp(500, b"<html>"))
    monkeypatch.setattr(compositor, "_local_alpha",
                        lambda i: (_ for _ in ()).throw(AssertionError("로컬 금지")))
    with pytest.raises(httpx.HTTPStatusError):
        compositor._cutout_alpha_impl(Image.new("RGB", (4, 3)), backend="fal", fallback=False)


def test_fal_alpha_real_response_200_reads_mask(monkeypatch):
    buf = io.BytesIO()
    Image.new("L", (2, 2), 200).save(buf, format="PNG")
    _fal_alpha_http(monkeypatch, _http_resp(200, buf.getvalue()))
    a = compositor._fal_alpha(Image.new("RGB", (4, 3)))
    assert a.shape == (3, 4) and int(a[0, 0]) == 200


# ══ find_cover / compose_flat: 책·음반 표지 펴기 ══════════════
import cv2

# 원근으로 기운 표지 — 위가 좁은 사다리꼴 (좌상·우상·우하·좌하)
TILTED = np.array([[130, 60], [290, 80], [320, 330], [90, 310]], np.int32)


def _poly_alpha(pts, h=400, w=400):
    a = np.zeros((h, w), np.uint8)
    cv2.fillPoly(a, [np.asarray(pts, np.int32)], 255)
    return a


def _cover_img(pts=TILTED, h=400, w=400, bg=(120, 120, 120), fill=(20, 40, 220)):
    """회색 바닥 위 기운 표지 — 표지 안쪽은 단색."""
    arr = np.full((h, w, 3), bg, np.uint8)
    cv2.fillPoly(arr, [np.asarray(pts, np.int32)], fill)
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def _patch_alpha(monkeypatch, alpha):
    seen = []
    monkeypatch.setattr(compositor, "original_alpha",
                        lambda b, img: seen.append(img.size) or alpha)
    return seen


def _signed_area(q):
    x, y = q[:, 0], q[:, 1]
    return float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y))


def test_order_corners_any_start_and_direction():
    """둘레 순서 입력 — 시작점이 어디든, 방향이 반대든 좌상·우상·우하·좌하."""
    pts = np.array([[10, 10], [100, 12], [95, 80], [5, 90]], np.float32)
    for k in range(4):
        for cand in (np.roll(pts, k, axis=0), np.roll(pts, k, axis=0)[::-1]):
            out = compositor._order_corners(cand.copy())
            assert out.dtype == np.float32 and out.shape == (4, 2)
            assert np.array_equal(out, pts)


def test_order_corners_diamond_gives_four_distinct_points():
    """45도 돌아간 정사각형 — 예전 합·차 방식은 같은 점을 두 번 골랐다."""
    q = np.array([[200, 50], [350, 200], [200, 350], [50, 200]], np.float32)
    for cand in (q, q[::-1], np.roll(q, 1, axis=0)):
        out = compositor._order_corners(cand.copy())
        assert len({tuple(p) for p in out}) == 4
        assert _signed_area(out) > 0                  # 화면 좌표에서 시계 방향(좌상→우상→우하)
        assert out.sum(1)[0] == out.sum(1).min()      # x+y 최소점부터


def test_find_cover_tilted_quad_returns_ordered_corners():
    q = compositor.find_cover(_poly_alpha(TILTED))
    assert q is not None and q.shape == (4, 2)
    assert np.abs(q - TILTED).max() <= 3          # 좌상·우상·우하·좌하 순서로 대략 맞게


def test_find_cover_axis_aligned_rect():
    q = compositor.find_cover(_alpha())             # 100x60 사각형 (50,20)-(150,80)
    assert q is not None
    assert np.abs(q - np.array([[50, 20], [149, 20], [149, 79], [50, 79]])).max() <= 2


@pytest.mark.parametrize("axes", [(120, 120), (150, 80)])
def test_find_cover_circle_or_ellipse_is_none(axes):
    a = np.zeros((400, 400), np.uint8)
    cv2.ellipse(a, (200, 200), axes, 0, 0, 360, 255, -1)
    assert compositor.find_cover(a) is None


DIAMOND = np.array([[200, 50], [350, 200], [200, 350], [50, 200]], np.int32)


def test_find_cover_45_degree_square_four_distinct_corners():
    q = compositor.find_cover(_poly_alpha(DIAMOND))
    assert q is not None and len({tuple(np.round(p)) for p in q}) == 4
    assert _signed_area(q) > 0
    for p in DIAMOND:                                 # 네 꼭짓점 모두 근처에 하나씩
        assert np.linalg.norm(q - p, axis=1).min() <= 3


def test_find_cover_two_books_is_none():
    """떨어진 물건 둘(여러 권) — 한 권만 펴면 나머지가 사라지므로 None."""
    a = np.zeros((400, 600), np.uint8)
    a[50:350, 30:260] = 255
    a[80:330, 330:560] = 255
    assert compositor.find_cover(a) is None


def test_find_cover_tiny_speck_besides_cover_still_found():
    """clean_alpha 가 남기지 않는 작은 조각(5% 미만)은 여러 권으로 보지 않는다."""
    a = np.zeros((400, 600), np.uint8)
    a[50:350, 30:260] = 255
    a[10:14, 500:504] = 255
    assert compositor.find_cover(a) is not None


def test_find_cover_short_side_below_min_is_none():
    s = compositor.FLAT_MIN_SIDE
    a = np.zeros((400, 400), np.uint8)
    a[100:100 + s - 4, 50:350] = 255                 # 가는 띠
    assert compositor.find_cover(a) is None
    a[:] = 0
    a[100:100 + s + 4, 50:350] = 255                 # 기준보다 조금 두꺼우면 찾는다
    assert compositor.find_cover(a) is not None


def test_unwarp_axis_aligned_rect_is_crop():
    arr = np.zeros((100, 200, 3), np.uint8)
    arr[20:80, 50:150] = (10, 200, 30)
    q = np.array([[50, 20], [149, 20], [149, 79], [50, 79]], np.float32)
    out = compositor._unwarp(Image.fromarray(arr), q)
    assert out.size == (99, 59)                      # 마주 보는 변 중 긴 쪽 길이 (int)
    o = np.asarray(out).astype(int)
    assert np.abs(o - (10, 200, 30)).max() <= 2      # 표지 안쪽만 — 바닥이 섞이지 않는다


def test_unwarp_tilted_uses_longer_opposite_sides():
    q = TILTED.astype(np.float32)
    out = compositor._unwarp(Image.new("RGB", (400, 400)), q)
    w = int(max(np.linalg.norm(q[1] - q[0]), np.linalg.norm(q[2] - q[3])))
    h = int(max(np.linalg.norm(q[3] - q[0]), np.linalg.norm(q[2] - q[1])))
    assert out.size == (w, h)


def test_find_cover_empty_alpha_is_none():
    assert compositor.find_cover(np.zeros((100, 200), np.uint8)) is None


def test_find_cover_low_alpha_only_is_none():
    """128 미만(반투명 잔여물)만 있으면 물건이 없는 것."""
    assert compositor.find_cover(np.full((100, 200), 100, np.uint8)) is None


def test_find_cover_small_protrusion_still_found():
    """비닐이 살짝 삐져나온 표지 — fill 이 FLAT_FILL 안이면 네 모서리를 찾는다."""
    a = _poly_alpha(TILTED)
    cv2.fillPoly(a, [np.array([[200, 70], [215, 55], [230, 72]], np.int32)], 255)   # 위쪽 작은 돌기
    q = compositor.find_cover(a)
    assert q is not None
    assert np.abs(q - TILTED).max() <= 8


def test_find_cover_large_protrusion_is_none():
    """펼친 책·비닐 크게 삐져나옴 — 네 모서리 면적과 오리기 면적이 크게 다르면 None."""
    a = np.zeros((400, 400), np.uint8)
    a[100:300, 100:300] = 255
    cv2.fillPoly(a, [np.array([[100, 100], [200, 20], [300, 100]], np.int32)], 255)  # 지붕
    assert compositor.find_cover(a) is None


def test_find_cover_ignores_blob_outside_item_box():
    """박스 밖 큰 조각(표지보다 큼)은 clean_alpha 로 지워져 표지를 찾는다."""
    a = np.zeros((400, 800), np.uint8)
    a[100:300, 50:250] = 255                        # 표지 (200x200)
    cv2.circle(a, (600, 200), 150, 255, -1)          # 박스 밖 더 큰 원
    assert compositor.find_cover(a) is None          # 박스 없으면 큰 원이 이긴다 → 사각형 아님
    q = compositor.find_cover(a, {"x1": 0, "y1": 0, "x2": 400, "y2": 1000})
    assert q is not None
    assert np.abs(q - np.array([[50, 100], [249, 100], [249, 299], [50, 299]])).max() <= 2


def test_compose_flat_rectifies_tilted_cover(monkeypatch):
    seen = _patch_alpha(monkeypatch, _poly_alpha(TILTED))
    bg = (240, 230, 220)
    raw = compositor.compose_flat(_cover_img(), bg)
    assert raw[:3] == b"\xff\xd8\xff"               # JPEG
    out = Image.open(io.BytesIO(raw))
    assert out.format == "JPEG" and out.size == (compositor.CANVAS, compositor.CANVAS)
    assert seen == [(400, 400)]
    arr = np.asarray(out.convert("RGB")).astype(int)
    assert np.abs(arr[5, 5] - bg).max() <= 3        # 모서리 = 배경색
    assert np.abs(arr[-5, 5] - bg).max() <= 3
    blue = (arr[:, :, 2] > 150) & (arr[:, :, 0] < 90) & (arr[:, :, 1] < 110)
    ys, xs = np.where(blue)
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), ys.max()
    # 원근이 펴져 축에 평행한 직사각형 — bbox 를 거의 꽉 채운다
    assert blue[y1:y2 + 1, x1:x2 + 1].mean() > 0.97
    # 가운데 (좌우·상하 여백이 같다)
    c = compositor.CANVAS
    assert abs(x1 - (c - 1 - x2)) <= 4 and abs(y1 - (c - 1 - y2)) <= 4
    # 크기 = 모서리 길이 × min(여백 안, MAX_UPSCALE)
    t = TILTED.astype(float)
    w = int(max(np.linalg.norm(t[1] - t[0]), np.linalg.norm(t[2] - t[3])))
    h = int(max(np.linalg.norm(t[3] - t[0]), np.linalg.norm(t[2] - t[1])))
    scale = min(c * (1 - 2 * compositor.MARGIN) / max(w, h), compositor.MAX_UPSCALE)
    assert abs((x2 - x1 + 1) - w * scale) <= 4 and abs((y2 - y1 + 1) - h * scale) <= 4
    # 세로가 더 긴 표지 → 결과도 세로가 길다
    assert (y2 - y1) > (x2 - x1)


def test_compose_flat_shadow_is_darker_than_bg_bottom_right(monkeypatch):
    _patch_alpha(monkeypatch, _poly_alpha(TILTED))
    bg = (240, 240, 240)
    arr = np.asarray(Image.open(io.BytesIO(compositor.compose_flat(_cover_img(), bg))).convert("RGB"))
    blue = (arr[:, :, 2] > 150) & (arr[:, :, 0] < 90)
    ys, xs = np.where(blue)
    below = arr[ys.max() + 4, (xs.min() + xs.max()) // 2]   # 표지 바로 아래 = 그림자
    assert below.mean() < 235


def test_compose_flat_falls_back_to_compose_with_alpha(monkeypatch):
    a = np.zeros((400, 400), np.uint8)
    cv2.circle(a, (200, 200), 120, 255, -1)          # 원 → 네 모서리 없음
    _patch_alpha(monkeypatch, a)
    calls = []
    monkeypatch.setattr(compositor, "compose",
                        lambda b, bg, box=None, alpha=None: calls.append((b, bg, box, alpha)) or b"FALLBACK")
    img = _cover_img()
    box = {"x1": 0, "y1": 0, "x2": 1000, "y2": 1000}
    assert compositor.compose_flat(img, (1, 2, 3), box) == b"FALLBACK"
    assert len(calls) == 1
    b, bg, got_box, got_alpha = calls[0]
    assert b == img and bg == (1, 2, 3) and got_box == box
    assert got_alpha is a                            # 다시 오리지 않게 알파를 넘긴다


@pytest.mark.parametrize("target", ["_unwarp", "find_cover"])
def test_compose_flat_exception_while_flattening_falls_back_to_compose(monkeypatch, target):
    a = _poly_alpha(TILTED)
    _patch_alpha(monkeypatch, a)

    def boom(*args, **kw):
        raise RuntimeError("cv2 exploded")
    monkeypatch.setattr(compositor, target, boom)
    calls = []
    monkeypatch.setattr(compositor, "compose",
                        lambda b, bg, box=None, alpha=None: calls.append(alpha) or b"FALLBACK")
    assert compositor.compose_flat(_cover_img(), (9, 9, 9)) == b"FALLBACK"
    assert len(calls) == 1 and calls[0] is a


def test_compose_flat_diamond_cover_is_flattened(monkeypatch):
    """45도 표지도 제대로 펴져 파란 정사각형이 나온다 (예전엔 퇴화 변환으로 빈 그림)."""
    _patch_alpha(monkeypatch, _poly_alpha(DIAMOND))
    raw = compositor.compose_flat(_cover_img(DIAMOND), (255, 255, 255))
    arr = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB")).astype(int)
    blue = (arr[:, :, 2] > 150) & (arr[:, :, 0] < 90)
    ys, xs = np.where(blue)
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), ys.max()
    assert blue[y1:y2 + 1, x1:x2 + 1].mean() > 0.97
    assert abs((x2 - x1) - (y2 - y1)) <= 4           # 정사각형


def test_compose_flat_two_books_falls_back_to_compose(monkeypatch):
    a = np.zeros((400, 600), np.uint8)
    a[50:350, 30:260] = 255
    a[80:330, 330:560] = 255
    _patch_alpha(monkeypatch, a)
    calls = []
    monkeypatch.setattr(compositor, "compose",
                        lambda b, bg, box=None, alpha=None: calls.append(alpha) or b"FALLBACK")
    assert compositor.compose_flat(_cover_img(h=400, w=600), (1, 1, 1)) == b"FALLBACK"
    assert calls[0] is a


def test_compose_flat_empty_alpha_falls_back_and_raises(monkeypatch):
    """네 모서리도, 물건도 없으면 compose 의 ValueError 가 그대로 올라간다 (pipeline 이 처리)."""
    _patch_alpha(monkeypatch, np.zeros((400, 400), np.uint8))
    with pytest.raises(ValueError):
        compositor.compose_flat(_cover_img(), (255, 255, 255))


def test_compose_flat_does_not_upscale_beyond_cap(monkeypatch):
    small = np.array([[20, 20], [60, 20], [60, 70], [20, 70]], np.int32)
    _patch_alpha(monkeypatch, _poly_alpha(small, 100, 100))
    raw = compositor.compose_flat(_cover_img(small, 100, 100), (255, 255, 255))
    arr = np.asarray(Image.open(io.BytesIO(raw)).convert("RGB")).astype(int)
    blue = (arr[:, :, 2] > 150) & (arr[:, :, 0] < 90)
    ys, _ = np.where(blue)
    assert ys.max() - ys.min() + 1 <= 50 * compositor.MAX_UPSCALE + 3
