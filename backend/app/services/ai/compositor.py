"""배경 교체 모드 — 원본 물건 픽셀을 그대로 오려 프리셋 배경 위에 합성한다.

생성 모델(generate)이 두 번 시도해도 하자·로고를 지키지 못할 때 쓰는 폴백.
물건 픽셀을 다시 그리지 않으므로 하자·글자 보존이 구조적으로 보장된다
(대신 물건의 구도·화질·조명은 원본 그대로).

오리기 (CUTOUT_BACKEND):
- "fal" (기본): fal-ai/birefnet/v2 "General Use (Light)" — 2026-09-24 비교에서 어수선한 배경의
  어두운 물건(커피메이커)도 경계가 깨끗했다. 1장 ~3-5초, 호출당 소액.
- "local": rembg `isnet-general-use` (CPU, 1장 ~5초) — fal 실패 시에도 이쪽으로 대체.
  어수선한 배경에선 벽 조각이 남고 윗부분이 잘리는 등 품질이 낮다. birefnet 로컬은 8GB 환경에서 메모리 부족.

isolate() 는 item_dino 가드용 — 원본·생성 결과에서 물건만 오려 같은 배경·크기에 놓는다.
생성 결과는 항상 로컬 rembg 로 오리므로, 가드가 켜지면 첫 생성 때 rembg 모델이 로드된다.
"""
import hashlib
import io
import logging
import os
import threading
from collections import OrderedDict

import numpy as np
from PIL import Image, ImageFilter

from app.core.config import settings

logger = logging.getLogger("carret.compositor")
FAL_CUTOUT = "fal-ai/birefnet/v2"

_MODEL_NAME = "isnet-general-use"
_session = None
_lock = threading.Lock()        # 알파 캐시
_load_lock = threading.Lock()   # rembg 모델 로드 (수 초) — 캐시 조회를 막지 않게 따로

CANVAS = 1024          # 생성 결과와 같은 정사각 크기
MARGIN = 0.08          # 물건 주변 여백 (캔버스 비율)
MAX_UPSCALE = 2.5      # 저해상도 원본을 과하게 키워 뭉개지지 않게


def _load():
    """싱글톤 (double-checked locking) — embedder._load 와 같은 이유."""
    global _session
    if _session is None:
        with _load_lock:
            if _session is None:
                from rembg import new_session
                _session = new_session(_MODEL_NAME)
    return _session


def _local_alpha(img: Image.Image) -> np.ndarray:
    from rembg import remove
    cut = remove(img, session=_load())
    return np.asarray(cut.split()[-1], dtype=np.uint8)


def _fal_alpha(img: Image.Image) -> np.ndarray:
    import fal_client
    import httpx
    from app.services.ai.generator import _upload
    os.environ.setdefault("FAL_KEY", settings.fal_key)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    r = fal_client.subscribe(FAL_CUTOUT, arguments={
        "image_url": _upload(buf.getvalue()), "model": "General Use (Light)", "mask_only": True},
        client_timeout=60)   # 멈춘 호출이 스레드를 무한정 잡지 않게
    resp = httpx.get(r["image"]["url"], timeout=60, follow_redirects=True)
    resp.raise_for_status()   # 에러 페이지(HTML)를 마스크로 읽지 않게 — 실패하면 호출부가 로컬로 대체
    mask = Image.open(io.BytesIO(resp.content)).convert("L")
    return np.asarray(mask.resize(img.size, Image.LANCZOS), dtype=np.uint8)


def cutout_alpha(img: Image.Image, backend: str | None = None,
                 fallback: bool = True) -> np.ndarray:
    """RGB 이미지 → 물건 알파 (0-255, HxW). fal 우선, 실패하면 로컬.
    backend 를 주면 설정 대신 그걸 쓴다. fallback=False 면 fal 실패를 그대로 올린다."""
    if (backend or settings.cutout_backend) == "fal":
        try:
            return _fal_alpha(img)
        except Exception as e:
            if not fallback:
                raise
            logger.warning(f"fal 오리기 실패, 로컬로 대체: {e}")
    return _local_alpha(img)


_cutout_alpha_impl = cutout_alpha   # 테스트용 원본 참조 (conftest 가 cutout_alpha 를 막아도 분기 검증 가능)

_ALPHA_CACHE: OrderedDict = OrderedDict()
_ALPHA_CACHE_MAX = 8   # 원본 해상도 알파(12MP ≈ 12MB)라 항목 수를 작게


def original_alpha(image_bytes: bytes, img: Image.Image) -> np.ndarray:
    """원본 알파 캐시 — 한 변환 안에서 item_dino 가드(생성 시도마다)와 배경 교체 모드가 같은
    원본을 오린다 (fal 이면 호출마다 비용). 키에 백엔드 설정과 cutout_alpha 자체도 넣는다
    (설정이 바뀌거나 테스트가 바꿔치기했을 때 옛 값이 섞이지 않게).

    fal 이 실패해 로컬로 대체한 알파는 캐시하지 않는다 — 품질이 낮은 오리기가 캐시에
    남아 배경 교체 모드(정직성 폴백)까지 계속 쓰게 되면 안 된다. 다음 호출에서 fal 을 다시 시도."""
    backend = settings.cutout_backend
    key = (hashlib.sha1(image_bytes).digest(), backend, cutout_alpha)
    with _lock:
        if key in _ALPHA_CACHE:
            _ALPHA_CACHE.move_to_end(key)
            return _ALPHA_CACHE[key]
    try:
        alpha = cutout_alpha(img, fallback=False)
    except Exception as e:
        if backend != "fal":
            raise
        logger.warning(f"fal 오리기 실패, 로컬로 대체(캐시 안 함): {e}")
        return cutout_alpha(img, backend="local")
    if hasattr(alpha, "setflags"):
        alpha.setflags(write=False)   # 공유 캐시 — clean_alpha 는 복사본에서 작업한다
    with _lock:
        _ALPHA_CACHE[key] = alpha
        while len(_ALPHA_CACHE) > _ALPHA_CACHE_MAX:
            _ALPHA_CACHE.popitem(last=False)
    return alpha


def clean_alpha(alpha: np.ndarray, item_box: dict | None = None) -> np.ndarray:
    """오리기 잔여물 정리: 물건 박스 밖은 지우고, 가장 큰 덩어리(와 그 5% 이상인
    덩어리)만 남긴다 — 어수선한 배경에서 벽 조각이 뿌옇게 남는 문제 대응."""
    import cv2
    a = alpha.copy()
    h, w = a.shape
    if item_box:
        item_box = {k: int(round(float(item_box[k]))) for k in ("x1", "y1", "x2", "y2")}
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


def _item_and_mask(img: Image.Image, alpha: np.ndarray,
                   item_box: dict | None) -> tuple[Image.Image, Image.Image]:
    """알파를 정리하고 물건이 있는 영역만 잘라 (물건, 마스크) 로."""
    alpha = clean_alpha(alpha, item_box)
    ys, xs = np.nonzero(alpha >= 128)
    if len(xs) == 0:
        raise ValueError("물건을 찾지 못함 (알파가 비어 있음)")
    box = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
    return img.crop(box), Image.fromarray(alpha).crop(box)


ISOLATE_SIZE = 448          # DINO 입력 크기 근처 — 더 크게 만들어도 모델이 줄인다
ISOLATE_BG = (128, 128, 128)  # 중립 회색 — 프리셋 배경색(흰색/나무/회색)이 비교에 섞이지 않게


def isolate(image_bytes: bytes, item_box: dict | None = None, original: bool = False) -> bytes:
    """물건만 오려 같은 크기의 중립 배경 가운데에 놓은 PNG (그림자 없음) — 원본과 결과의
    물건끼리만 DINO 로 비교하기 위한 것. 위치·크기·배경 차이는 여기서 빠진다.

    original=True 면 설정의 오리기(fal 우선)를 캐시해서 쓰고 — 원본은 배경이 어수선하다 —
    아니면(생성 결과) 로컬 rembg 로 오린다. 생성 결과도 프리셋에 따라(warm_wood 의 나무
    탁자·소품) 배경이 깨끗하지 않을 수 있어, 남은 배경 조각이 유사도를 낮출 수 있다 —
    item_dino 기준값은 프리셋별 분포를 보고 정해야 한다."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    alpha = original_alpha(image_bytes, img) if original else cutout_alpha(img, backend="local")
    item, mask = _item_and_mask(img, alpha, item_box)
    room = ISOLATE_SIZE * 0.9
    scale = min(room / item.width, room / item.height)
    size = (max(1, round(item.width * scale)), max(1, round(item.height * scale)))
    canvas = Image.new("RGB", (ISOLATE_SIZE, ISOLATE_SIZE), ISOLATE_BG)
    canvas.paste(item.resize(size, Image.LANCZOS),
                 ((ISOLATE_SIZE - size[0]) // 2, (ISOLATE_SIZE - size[1]) // 2),
                 mask.resize(size, Image.LANCZOS))
    buf = io.BytesIO()
    canvas.save(buf, format="PNG")
    return buf.getvalue()


def compose(image_bytes: bytes, bg_color: tuple, item_box: dict | None = None,
            alpha: np.ndarray | None = None) -> bytes:
    """원본 → 물건만 오려 CANVAS 정사각 배경 가운데에 놓고 바닥 그림자를 깐 JPEG.
    물건 픽셀은 크기 조정(리샘플링) 외에는 손대지 않는다."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if alpha is None:
        alpha = original_alpha(image_bytes, img)
    item, mask = _item_and_mask(img, alpha, item_box)

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
