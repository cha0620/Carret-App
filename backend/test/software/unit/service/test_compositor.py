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
