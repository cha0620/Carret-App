"""compositor.arrange · _cells — 여러 물건을 한 장에 (10-04, 생성 없음).

오리기(original_alpha)는 가짜로 — 흰 배경이 아닌 픽셀을 물건으로 본다 (fal · rembg 호출 없음).
"""
import io

import numpy as np
import pytest
from PIL import Image

from app.services.ai import compositor

BG = (245, 245, 245)


def _png(color, size=(200, 200), box=(50, 50, 150, 150), extra=None):
    """흰 배경 위 색 사각형 하나 (extra=(색, 박스) 로 하나 더)."""
    img = Image.new("RGB", size, (255, 255, 255))
    img.paste(color, box)
    if extra:
        img.paste(extra[0], extra[1])
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture()
def fake_alpha(monkeypatch):
    """흰색(>=250)이 아닌 곳 = 물건. 호출 횟수를 센다."""
    calls = []

    def fake(image_bytes, img):
        calls.append(image_bytes)
        a = np.asarray(img.convert("RGB")).astype(int)
        return np.where((a >= 250).all(axis=2), 0, 255).astype(np.uint8)
    monkeypatch.setattr(compositor, "original_alpha", fake)
    return calls


def _open(b):
    return np.asarray(Image.open(io.BytesIO(b)).convert("RGB")).astype(int)


def _mask(arr, color):
    """그 색에 가까운 픽셀 (JPEG 손실 감안)."""
    return (np.abs(arr - np.array(color)).sum(axis=2) < 90)


def _bbox(m):
    ys, xs = np.nonzero(m)
    return xs.min(), ys.min(), xs.max(), ys.max()


RED, BLUE, GREEN = (220, 30, 30), (30, 30, 220), (30, 200, 30)


@pytest.mark.parametrize("n,cols,rows", [(3, 2, 2), (4, 2, 2), (5, 3, 2),])
def test_cells_grid_shape(n, cols, rows):
    cells = compositor._cells(n, "grid")
    assert len(cells) == n
    assert all(c[2] == pytest.approx(1 / cols) and c[3] == pytest.approx(1 / rows) for c in cells)
    assert sorted({round(c[1], 6) for c in cells}) == [round(r / rows, 6) for r in range(rows)]


def test_cells_grid_last_row_centered():
    c3 = compositor._cells(3, "grid")
    assert c3[:2] == [(0.0, 0.0, 0.5, 0.5), (0.5, 0.0, 0.5, 0.5)]
    assert c3[2] == pytest.approx((0.25, 0.5, 0.5, 0.5))           # 아래 한 개는 가운데
    c5 = compositor._cells(5, "grid")
    assert [c[0] for c in c5[3:]] == pytest.approx([1 / 6, 1 / 6 + 1 / 3])
    # 마지막 줄 무리의 가운데 = 0.5
    last = c5[3:]
    assert (last[0][0] + last[-1][0] + last[-1][2]) / 2 == pytest.approx(0.5)


@pytest.mark.parametrize("n", [2, 3, 6])
def test_cells_overlap_thirty_percent_and_fills_width(n):
    cells = compositor._cells(n, "overlap")
    w = cells[0][2]
    assert all(c[2] == pytest.approx(w) and c[1] == 0 and c[3] == 1 for c in cells)
    for a, b in zip(cells, cells[1:]):
        assert b[0] - a[0] == pytest.approx(0.7 * w)                # 앞 칸의 70% 에서 시작 → 30% 겹침
    assert cells[-1][0] + w == pytest.approx(1.0)


def test_arrange_unknown_layout_raises_before_cutting(fake_alpha):
    with pytest.raises(ValueError, match="모르는 배치"):
        compositor.arrange([(_png(RED), None)], BG, "circle")
    assert fake_alpha == []


def test_arrange_row_two_items_canvas_and_order(fake_alpha):
    out = compositor.arrange([(_png(RED), None), (_png(BLUE), None)], BG, "row")
    assert out[:2] == b"\xff\xd8"
    arr = _open(out)
    assert arr.shape == (compositor.CANVAS, compositor.CANVAS, 3)
    assert tuple(arr[3, 3]) == pytest.approx(BG, abs=3)              # 모서리 = 배경
    r, b = _bbox(_mask(arr, RED)), _bbox(_mask(arr, BLUE))
    assert r[2] < b[0]                                               # 빨강이 왼쪽, 겹치지 않음
    assert abs(r[3] - b[3]) <= 2                                     # 바닥 선을 맞춘다
    assert len(fake_alpha) == 2


def test_arrange_overlap_first_item_on_top(fake_alpha):
    """겹칠 때 첫 물건(보통 대표)이 맨 위 — 가장 덜 가려지게."""
    big = dict(size=(600, 600), box=(50, 50, 550, 550))
    arr = _open(compositor.arrange([(_png(RED, **big), None), (_png(BLUE, **big), None)], BG, "overlap"))
    r, b = _bbox(_mask(arr, RED)), _bbox(_mask(arr, BLUE))
    red_w, blue_w = r[2] - r[0], b[2] - b[0]
    assert abs(b[0] - (r[2] + 1)) <= 3                              # 파랑은 빨강 끝에서부터 보인다
    assert blue_w < red_w * 0.85                                    # 파랑이 가려졌다
    row = (r[1] + r[3]) // 2
    assert _mask(arr, RED)[row, r[2] - 2]                           # 경계 바로 왼쪽은 빨강


def test_arrange_single_keeps_largest_blob_only(fake_alpha):
    """single 이면 가장 큰 덩어리 하나만 — 같이 찍힌 작은 물건(파랑)은 빠진다."""
    two = _png(RED, box=(10, 40, 110, 160), extra=(BLUE, (140, 80, 190, 130)))
    arr = _open(compositor.arrange([{"image": two, "single": True}, {"image": _png(GREEN)}], BG, "row"))
    assert _mask(arr, RED).sum() > 1000 and _mask(arr, BLUE).sum() == 0
    arr2 = _open(compositor.arrange([{"image": two, "single": False}, {"image": _png(GREEN)}], BG, "row"))
    assert _mask(arr2, BLUE).sum() > 100                            # single 이 아니면 둘 다 남는다


def test_arrange_drop_and_keep_boxes(fake_alpha):
    two = _png(RED, box=(10, 50, 80, 150), extra=(BLUE, (120, 50, 190, 150)))
    right = {"x1": 550, "y1": 0, "x2": 1000, "y2": 1000}
    arr = _open(compositor.arrange([{"image": two, "drop": [right]}, {"image": _png(GREEN)}], BG, "row"))
    assert _mask(arr, RED).sum() > 1000 and _mask(arr, BLUE).sum() == 0
    keep = {"x1": 580, "y1": 200, "x2": 1000, "y2": 800}           # 지울 박스 안이어도 파는 물건 박스는 남긴다
    arr2 = _open(compositor.arrange([{"image": two, "drop": [right], "keep": [keep]}, {"image": _png(GREEN)}],
                                    BG, "row"))
    assert _mask(arr2, BLUE).sum() > 100


def test_arrange_large_original_downscaled_same_layout(fake_alpha):
    """아주 큰 원본도 결과 크기 · 위치는 작은 원본과 비슷하다 (미리 줄여 들고 있어도)."""
    small = _open(compositor.arrange([(_png(RED, size=(400, 400), box=(50, 50, 350, 350)), None),
                                      (_png(BLUE), None)], BG, "row"))
    big = _open(compositor.arrange([(_png(RED, size=(4000, 4000), box=(500, 500, 3500, 3500)), None),
                                    (_png(BLUE), None)], BG, "row"))
    a, b = _bbox(_mask(small, RED)), _bbox(_mask(big, RED))
    assert all(abs(x - y) <= 4 for x, y in zip(a, b))


def test_arrange_uses_item_box(fake_alpha):
    """사진에 물건이 둘이어도 item_box 안의 것만 오린다."""
    two = _png(RED, box=(10, 50, 80, 150), extra=(BLUE, (120, 50, 190, 150)))
    left = {"x1": 0, "y1": 0, "x2": 420, "y2": 1000}
    arr = _open(compositor.arrange([(two, left), (_png(GREEN), None)], BG, "row"))
    assert _mask(arr, RED).sum() > 1000 and _mask(arr, BLUE).sum() == 0
    assert _mask(arr, GREEN).sum() > 1000


