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


def test_clean_alpha_drops_small_blobs_and_outside_item_box():
    a = _alpha()
    a[0:5, 0:5] = 255                                # 떨어진 작은 조각
    cleaned = compositor.clean_alpha(a)
    assert cleaned[2, 2] == 0 and cleaned[50, 100] == 255
    boxed = compositor.clean_alpha(_alpha(), {"x1": 0, "y1": 0, "x2": 400, "y2": 1000})
    assert boxed[50, 60] == 255 and boxed[50, 140] == 0   # 박스(가로 0~40%+여유) 밖은 지움


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


def test_compose_after_isolate_reuses_original_cutout(monkeypatch):
    """상품 가드(isolate original=True)가 오린 원본을 배경 교체가 다시 오리지 않는다."""
    calls = _counting_cutout(monkeypatch)
    b = _img()
    compositor.isolate(b, original=True)
    compositor.compose(b, (245, 245, 245))
    assert len(calls) == 1


# ══ isolate: 물건만 오려 회색 배경 가운데에 ══════════════
def _red_bbox(png):
    arr = np.asarray(_open(png)).astype(int)
    ys, xs = np.nonzero((arr[:, :, 0] > 150) & (arr[:, :, 1] < 80) & (arr[:, :, 2] < 80))
    return xs.min(), ys.min(), xs.max(), ys.max()


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


def test_compose_after_fal_fallback_retries_fal(monkeypatch):
    """배경 교체(정직성 폴백)가 상품 가드 때의 저품질 대체 알파를 물려받지 않는다."""
    used = _real_cutout(monkeypatch, "fal", [RuntimeError("fal down"), 255])
    monkeypatch.setattr(compositor, "_local_alpha",   # 불투명 알파라야 isolate 가 물건을 찾는다
                        lambda img: used.append("local") or np.full((img.size[1], img.size[0]), 255, np.uint8))
    b = _img()
    compositor.isolate(b, original=True)            # fal 실패 → local 로 isolate
    compositor.compose(b, (245, 245, 245))          # fal 재시도
    assert used == ["fal", "local", "fal"]


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


def test_order_corners_diamond_gives_four_distinct_points():
    """45도 돌아간 정사각형 — 예전 합·차 방식은 같은 점을 두 번 골랐다."""
    q = np.array([[200, 50], [350, 200], [200, 350], [50, 200]], np.float32)
    for cand in (q, q[::-1], np.roll(q, 1, axis=0)):
        out = compositor._order_corners(cand.copy())
        assert len({tuple(p) for p in out}) == 4
        assert _signed_area(out) > 0                  # 화면 좌표에서 시계 방향(좌상→우상→우하)
        assert out.sum(1)[0] == out.sum(1).min()      # x+y 최소점부터


DIAMOND = np.array([[200, 50], [350, 200], [200, 350], [50, 200]], np.int32)


def test_find_cover_two_books_is_none():
    """떨어진 물건 둘(여러 권) — 한 권만 펴면 나머지가 사라지므로 None."""
    a = np.zeros((400, 600), np.uint8)
    a[50:350, 30:260] = 255
    a[80:330, 330:560] = 255
    assert compositor.find_cover(a) is None


def test_find_cover_large_protrusion_is_none():
    """펼친 책·비닐 크게 삐져나옴 — 네 모서리 면적과 오리기 면적이 크게 다르면 None."""
    a = np.zeros((400, 400), np.uint8)
    a[100:300, 100:300] = 255
    cv2.fillPoly(a, [np.array([[100, 100], [200, 20], [300, 100]], np.int32)], 255)  # 지붕
    assert compositor.find_cover(a) is None


def test_compose_flat_rectifies_tilted_cover(monkeypatch):
    seen = _patch_alpha(monkeypatch, _poly_alpha(TILTED))
    bg = (240, 230, 220)
    raw = compositor.compose_flat(_cover_img(), bg)
    assert raw[:3] == b"\xff\xd8\xff"               # JPEG
    out = Image.open(io.BytesIO(raw))
    assert out.format == "JPEG" and out.size == (compositor.CANVAS, compositor.CANVAS)
    assert seen == [(400, 400)]
    arr = np.asarray(out.convert("RGB")).astype(int)
    c = compositor.CANVAS
    # 모서리 = 배경(스튜디오 스윕 — 프리셋 색 근처, 위는 밝고 아래는 어둡다)
    assert np.abs(arr[5, 5] - bg).max() <= 20 and np.abs(arr[-5, 5] - bg).max() <= 50
    assert arr[5, c // 2].mean() > arr[-5, c // 2].mean()
    blue = (arr[:, :, 2] > 150) & (arr[:, :, 0] < 90) & (arr[:, :, 1] < 110)
    ys, xs = np.where(blue)
    x1, x2, y1, y2 = xs.min(), xs.max(), ys.min(), ys.max()
    # 원근이 펴져 축에 평행한 직사각형 — bbox 를 거의 꽉 채운다
    assert blue[y1:y2 + 1, x1:x2 + 1].mean() > 0.97
    # 좌우 가운데, 세로는 가운데보다 FLAT_DROP 만큼 아래 (바닥에 놓인 느낌)
    assert abs(x1 - (c - 1 - x2)) <= 4
    assert abs((y1 + y2) / 2 - (c / 2 + c * compositor.FLAT_DROP)) <= 4
    # 크기 = 모서리 길이 × min(여백 안, MAX_UPSCALE)
    t = TILTED.astype(float)
    w = int(max(np.linalg.norm(t[1] - t[0]), np.linalg.norm(t[2] - t[3])))
    h = int(max(np.linalg.norm(t[3] - t[0]), np.linalg.norm(t[2] - t[1])))
    scale = min(c * (1 - 2 * compositor.FLAT_MARGIN) / max(w, h), compositor.MAX_UPSCALE)
    assert abs((x2 - x1 + 1) - w * scale) <= 4 and abs((y2 - y1 + 1) - h * scale) <= 4
    # 세로가 더 긴 표지 → 결과도 세로가 길다
    assert (y2 - y1) > (x2 - x1)


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


def test_compose_flat_empty_alpha_falls_back_and_raises(monkeypatch):
    """네 모서리도, 물건도 없으면 compose 의 ValueError 가 그대로 올라간다 (pipeline 이 처리)."""
    _patch_alpha(monkeypatch, np.zeros((400, 400), np.uint8))
    with pytest.raises(ValueError):
        compositor.compose_flat(_cover_img(), (255, 255, 255))


def test_clean_alpha_flat_drops_thin_attachment_but_keeps_item():
    """책·CD: 윤곽에 붙은 가는 막대(뒤쪽 물건)는 떼고 물건 본체는 그대로 (09-29 CD)."""
    a = np.zeros((400, 400), np.uint8)
    a[100:350, 50:350] = 255                         # 케이스
    a[20:100, 180:184] = 255                         # 위로 붙은 4px 막대
    kept = compositor.clean_alpha(a, flat=False)
    flat = compositor.clean_alpha(a, flat=True)
    assert (kept[20:100, 180:184] >= 128).all()      # 기본은 그대로 (자전거 살·끈을 지키려고)
    assert (flat[20:95, 180:184] < 128).all()
    assert (flat[100:350, 50:350] >= 128).mean() > 0.99


# ══ trim_page_edges: 누운 책의 책 배·옆면 띠 빼기 ══════════════
def _book_with_strip(strip_rgb, strip_h=30, lined=True):
    """회색 표지(300×400) 아래에 strip_h 높이 띠 — 표지 경계에 어두운 선, 띠에는 가는 줄."""
    img = np.full((400 + strip_h, 300, 3), 120, np.uint8)
    img[:, :, 2] = 140
    img[400:] = strip_rgb
    img[399:401] = 60                                  # 표지 아랫변 그림자 선
    if lined:
        img[402::4, :] = np.array(strip_rgb) * 0.85    # 종이 단면 줄
    q = np.array([[0, 0], [299, 0], [299, 399 + strip_h], [0, 399 + strip_h]], np.float32)
    return Image.fromarray(img), q


def test_trim_page_edges_drops_paper_strip_below_cover():
    img, q = _book_with_strip((225, 220, 210))
    out = compositor.trim_page_edges(img, q)
    assert abs(out[2][1] - 400) <= 4 and abs(out[3][1] - 400) <= 4   # 아랫변이 표지 경계로
    assert np.abs(out[:2] - q[:2]).max() <= 1                        # 윗변은 그대로


# ══ 10-03: drop_boxes — 팔지 않는다고 고른 물건 자리를 지운다 (고른 물건과 겹친 곳은 남김) ═══════
import pytest  # noqa: E402


def _full(h=100, w=200):
    return np.full((h, w), 255, np.uint8)


def test_drop_boxes_keep_wins_over_any_number_of_drops():
    box = {"x1": 250, "y1": 250, "x2": 750, "y2": 750}
    out = compositor.drop_boxes(_full(), [box, box, {"x1": 0, "y1": 0, "x2": 1000, "y2": 1000}], keep=[box])
    assert (out[25:75, 50:150] == 255).all() and out[0, 0] == 0 and out[99, 199] == 0


def test_compose_drop_removes_unchosen_item_from_canvas():
    """두 물건 중 오른쪽(파랑)을 빼면 결과에 파랑이 없다."""
    img = Image.new("RGB", (200, 100), (200, 30, 30))
    img.paste((30, 30, 200), (110, 0, 200, 100))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    a = np.zeros((100, 200), np.uint8)
    a[20:80, 10:90] = 255
    a[20:80, 120:190] = 255
    out = compositor.compose(buf.getvalue(), (255, 255, 255), alpha=a,
                             drop=[{"x1": 550, "y1": 0, "x2": 1000, "y2": 1000}])
    px = np.asarray(Image.open(io.BytesIO(out)).convert("RGB")).reshape(-1, 3).astype(int)
    blue = (px[:, 2] > 150) & (px[:, 0] < 80)
    red = (px[:, 0] > 150) & (px[:, 2] < 80)
    assert red.sum() > 1000 and blue.sum() == 0


