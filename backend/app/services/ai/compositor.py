"""배경 교체 모드 — 원본 물건 픽셀을 그대로 오려 프리셋 배경 위에 합성한다.

생성 모델(generate)이 두 번 시도해도 하자·로고를 지키지 못할 때 쓰는 폴백.
물건 픽셀을 다시 그리지 않으므로 하자·글자 보존이 구조적으로 보장된다
(대신 물건의 구도·화질·조명은 원본 그대로).

오리기: rembg `isnet-general-use` (로컬 CPU, 첫 로드 ~5초, 1장 ~5초). 2026-09-24 비교에서
u2net 보다 물건 형태(자전거 프레임 등)를 덜 잘라 먹었다. birefnet 은 8GB 환경에서 메모리 부족.
"""
import io
import threading

import numpy as np
from PIL import Image, ImageFilter

_MODEL_NAME = "isnet-general-use"
_session = None
_lock = threading.Lock()

CANVAS = 1024          # 생성 결과와 같은 정사각 크기
MARGIN = 0.08          # 물건 주변 여백 (캔버스 비율)
MAX_UPSCALE = 2.5      # 저해상도 원본을 과하게 키워 뭉개지지 않게


def _load():
    """싱글톤 (double-checked locking) — embedder._load 와 같은 이유."""
    global _session
    if _session is None:
        with _lock:
            if _session is None:
                from rembg import new_session
                _session = new_session(_MODEL_NAME)
    return _session


def cutout_alpha(img: Image.Image) -> np.ndarray:
    """RGB 이미지 → 물건 알파 (0-255, HxW)."""
    from rembg import remove
    cut = remove(img, session=_load())
    return np.asarray(cut.split()[-1], dtype=np.uint8)


def clean_alpha(alpha: np.ndarray, item_box: dict | None = None) -> np.ndarray:
    """오리기 잔여물 정리: 물건 박스 밖은 지우고, 가장 큰 덩어리(와 그 5% 이상인
    덩어리)만 남긴다 — 어수선한 배경에서 벽 조각이 뿌옇게 남는 문제 대응."""
    import cv2
    a = alpha.copy()
    h, w = a.shape
    if item_box:
        pad = 30   # 0-1000 기준 여유 — VLM 박스가 살짝 작게 잡혀도 물건을 자르지 않게
        x1 = max(0, (item_box["x1"] - pad) * w // 1000)
        y1 = max(0, (item_box["y1"] - pad) * h // 1000)
        x2 = min(w, (item_box["x2"] + pad) * w // 1000)
        y2 = min(h, (item_box["y2"] + pad) * h // 1000)
        box_mask = np.zeros_like(a)
        box_mask[y1:y2, x1:x2] = 1
        a = a * box_mask
    binary = (a >= 128).astype(np.uint8)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n <= 1:
        return a
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = {i + 1 for i, area in enumerate(areas) if area >= areas.max() * 0.05}
    keep_mask = np.isin(labels, list(keep))
    # 남긴 덩어리 주변의 반투명 가장자리는 살리고, 떨어져 나간 조각은 지운다
    near = cv2.dilate(keep_mask.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    return (a * near).astype(np.uint8)


def compose(image_bytes: bytes, bg_color: tuple, item_box: dict | None = None,
            alpha: np.ndarray | None = None) -> bytes:
    """원본 → 물건만 오려 CANVAS 정사각 배경 가운데에 놓고 바닥 그림자를 깐 JPEG.
    물건 픽셀은 크기 조정(리샘플링) 외에는 손대지 않는다."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if alpha is None:
        alpha = cutout_alpha(img)
    alpha = clean_alpha(alpha, item_box)
    ys, xs = np.nonzero(alpha >= 128)
    if len(xs) == 0:
        raise ValueError("물건을 찾지 못함 (알파가 비어 있음)")
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    item = img.crop(box)
    mask = Image.fromarray(alpha).crop(box)

    room = CANVAS * (1 - 2 * MARGIN)
    scale = min(room / item.width, room / item.height, MAX_UPSCALE)
    size = (max(1, round(item.width * scale)), max(1, round(item.height * scale)))
    item = item.resize(size, Image.LANCZOS)
    mask = mask.resize(size, Image.LANCZOS)

    canvas = Image.new("RGB", (CANVAS, CANVAS), tuple(bg_color))
    x = (CANVAS - size[0]) // 2
    y = (CANVAS - size[1]) // 2

    # 바닥 그림자: 물건 아래쪽 폭만큼 납작한 타원을 흐리게 — 떠 있는 느낌 방지
    shadow = Image.new("L", (CANVAS, CANVAS), 0)
    sw, sh = int(size[0] * 0.8), max(8, int(size[1] * 0.06))
    sx, sy = x + (size[0] - sw) // 2, y + size[1] - sh // 2
    from PIL import ImageDraw
    ImageDraw.Draw(shadow).ellipse((sx, sy, sx + sw, sy + sh), fill=90)
    shadow = shadow.filter(ImageFilter.GaussianBlur(max(4, sh)))
    dark = Image.new("RGB", (CANVAS, CANVAS), tuple(int(c * 0.55) for c in bg_color))
    canvas = Image.composite(dark, canvas, shadow)

    canvas.paste(item, (x, y), mask)
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    return buf.getvalue()
