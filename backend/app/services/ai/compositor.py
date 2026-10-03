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

from app.core.config import reveal, settings

logger = logging.getLogger("carret.compositor")
FAL_CUTOUT = "fal-ai/birefnet/v2"

_MODEL_NAME = "isnet-general-use"
_session = None
_lock = threading.Lock()        # 알파 캐시
_load_lock = threading.Lock()   # rembg 모델 로드 (수 초) — 캐시 조회를 막지 않게 따로

CANVAS = 1024          # 생성 결과와 같은 정사각 크기
MARGIN = 0.08          # 물건 주변 여백 (캔버스 비율)
MAX_UPSCALE = 2.5      # 저해상도 원본을 과하게 키워 뭉개지지 않게
FLAT_MARGIN = 0.15     # 표지 컷 여백 — 0.08 은 표지가 화면을 꽉 채워 스캔본처럼 보였다 (10-01)
FLAT_DROP = 0.03       # 표지를 가운데보다 살짝 아래로 (캔버스 비율) — 바닥에 놓인 느낌


def studio_canvas(bg_color: tuple, size: int = CANVAS) -> Image.Image:
    """단색 대신 스튜디오 스윕 — 위는 조금 밝고 바닥 쪽은 조금 어둡게, 가장자리는 아주 옅은 비네트.
    프리셋 색은 화면 가운데 높이 근처에 그대로 남는다 (어두운 무드 색에서도 같은 비율)."""
    bg = np.array(bg_color, float)
    top = np.minimum(255, bg + 8)
    floor = bg * 0.92
    y = np.linspace(0, 1, size)[:, None]
    t = np.clip((y - 0.45) / 0.5, 0, 1)
    t = t * t * (3 - 2 * t)                      # smoothstep
    col = top * (1 - t) + floor * t              # (size, 3)
    xx, yy = np.meshgrid(np.linspace(-1, 1, size), np.linspace(-1, 1, size))
    vig = 1 - 0.05 * np.clip(xx ** 2 + yy ** 2 - 0.4, 0, None)
    img = col[:, None, :] * vig[..., None]
    return Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))


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
    os.environ.setdefault("FAL_KEY", reveal(settings.fal_key))
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


THIN_FRAC = 0.03   # 가는 돌출부 제거 — 물건 짧은 변의 3% 보다 가는 조각 (최소 5px)


def _drop_thin(binary: np.ndarray) -> np.ndarray:
    """마스크 열기(opening)로 물건 윤곽에 붙은 가는 조각(뒤쪽 막대·다른 케이스 모서리)을 뗀다.
    책·CD 같은 납작한 인쇄물 전용 — 자전거 살·끈·케이블처럼 원래 가는 부분이 있는 물건엔 쓰지 않는다."""
    import cv2
    ys, xs = np.nonzero(binary)
    if len(xs) == 0:
        return binary
    k = max(5, int(round(min(np.ptp(ys), np.ptp(xs)) * THIN_FRAC))) | 1
    return cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                            cv2.getStructuringElement(cv2.MORPH_RECT, (k, k)))


def _px(b: dict, w: int, h: int) -> tuple[int, int, int, int]:
    return (max(0, int(b["x1"]) * w // 1000), max(0, int(b["y1"]) * h // 1000),
            min(w, int(b["x2"]) * w // 1000), min(h, int(b["y2"]) * h // 1000))


def drop_boxes(alpha: np.ndarray, drop: list[dict] | None, keep: list[dict] | None = None) -> np.ndarray:
    """사용자가 팔지 않는다고 고른 물건 자리를 지운다 (10-03) — 고른 물건 박스와 겹치는 곳은 남긴다."""
    if not drop:
        return alpha
    h, w = alpha.shape
    gone = np.zeros_like(alpha, dtype=bool)
    for b in drop:
        x1, y1, x2, y2 = _px(b, w, h)
        gone[y1:y2, x1:x2] = True
    for b in keep or []:
        x1, y1, x2, y2 = _px(b, w, h)
        gone[y1:y2, x1:x2] = False
    out = alpha.copy()
    out[gone] = 0
    return out


def clean_alpha(alpha: np.ndarray, item_box: dict | None = None, *, flat: bool = False) -> np.ndarray:
    """오리기 잔여물 정리: 물건 박스 밖은 지우고, 가장 큰 덩어리(와 그 5% 이상인
    덩어리)만 남긴다 — 어수선한 배경에서 벽 조각이 뿌옇게 남는 문제 대응.
    flat=True(책·CD): 윤곽에 붙은 가는 조각도 뗀다 (09-29 CD 뒤쪽 은색 막대). 투명 케이스 너머로
    비치는 배경은 물건의 일부라 여기서 못 지운다 — 알려진 한계."""
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
    if flat:
        binary = _drop_thin(binary)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    if n <= 1:
        return (a * binary).astype(np.uint8) if flat else a
    areas = stats[1:, cv2.CC_STAT_AREA]
    keep = {i + 1 for i, area in enumerate(areas) if area >= areas.max() * 0.05}
    keep_mask = np.isin(labels, list(keep))
    # 남긴 덩어리 주변의 반투명 가장자리는 살리고, 떨어져 나간 조각은 지운다
    near = cv2.dilate(keep_mask.astype(np.uint8), np.ones((7, 7), np.uint8)) > 0
    return (a * near).astype(np.uint8)


def _item_and_mask(img: Image.Image, alpha: np.ndarray, item_box: dict | None,
                   *, flat: bool = False) -> tuple[Image.Image, Image.Image]:
    """알파를 정리하고 물건이 있는 영역만 잘라 (물건, 마스크) 로."""
    alpha = clean_alpha(alpha, item_box, flat=flat)
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


FLAT_FILL = (0.93, 1.07)   # 오리기 면적 / 네 모서리 면적 — 벗어나면 사각형이 아니다 (펼친 책·비닐 크게 삐져나옴)


FLAT_MIN_SIDE = 32         # px — 이보다 짧은 변이 있으면 표지로 보지 않는다 (퇴화 사각형)


def _order_corners(q: np.ndarray) -> np.ndarray:
    """둘레 순서의 네 점 → 좌상·우상·우하·좌하. 둘레 순서를 그대로 쓰고 시작점(x+y 최소)과
    방향만 맞춘다 — 점마다 x+y·y-x 로 따로 고르면 45도 근처에서 같은 점을 두 번 고른다."""
    x, y = q[:, 0], q[:, 1]
    if float(np.dot(x, np.roll(y, -1)) - np.dot(np.roll(x, -1), y)) < 0:
        q = q[::-1]   # 화면 좌표(y 아래)에서 좌상→우상→우하 방향이 되게
    return np.roll(q, -int(q.sum(1).argmin()), axis=0).astype(np.float32)


def find_cover(alpha: np.ndarray, item_box: dict | None = None) -> np.ndarray | None:
    """오리기 알파에서 표지(사각형)의 네 모서리 — 볼록 껍질을 네 점이 될 때까지 단순화.
    오리기 모양이 사각형과 많이 다르거나, 떨어진 물건이 둘 이상이면(여러 권 — 한 권만 펴면
    나머지가 사라진다) None (호출부가 일반 배경 교체로)."""
    import cv2
    binary = (clean_alpha(alpha, item_box, flat=True) >= 128).astype(np.uint8)
    cnts, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    cnts = sorted(cnts, key=cv2.contourArea, reverse=True)
    if len(cnts) > 1 and cv2.contourArea(cnts[1]) >= cv2.contourArea(cnts[0]) * 0.05:
        return None
    c = cnts[0]
    hull = cv2.convexHull(c)
    peri = cv2.arcLength(hull, True)
    for eps in np.linspace(0.01, 0.1, 19):
        ap = cv2.approxPolyDP(hull, eps * peri, True)
        if len(ap) == 4:
            break
    else:
        return None
    q = _order_corners(ap.reshape(4, 2).astype(np.float32))
    if not cv2.isContourConvex(q.reshape(-1, 1, 2)):
        return None
    if min(np.linalg.norm(q - np.roll(q, -1, axis=0), axis=1)) < FLAT_MIN_SIDE:
        return None
    area = cv2.contourArea(q)
    if area <= 0 or not FLAT_FILL[0] <= cv2.contourArea(c) / area <= FLAT_FILL[1]:
        return None
    return q


PAGE_BAND = 0.10        # 가장자리에서 이 비율 안쪽까지만 책 배(종이 단면) 띠를 찾는다
PAGE_MIN_STRIP = 0.012  # 이보다 얇은 띠는 무시 (오리기 경계 흔들림)
PAGE_MIN_LEN = 0.6      # 표지 경계선은 그 변 길이의 이만큼 이어져야 한다
PAGE_MAX_TILT = 0.06    # 경계선 기울기 상한 (tan, 약 3.4도)
PAGE_MAX_SAT = 0.25     # 띠의 평균 채도 상한 — 종이 단면·책 옆면은 무채색. 색 있는 표지 무늬는 자르지 않는다


def _side_view(a: np.ndarray, side: str) -> np.ndarray:
    """편 표지 배열을 돌려 side 가 아래쪽에 오게 (행 = 가장자리에서 안쪽으로)."""
    return {"bottom": a, "top": a[::-1], "right": np.swapaxes(a, 0, 1),
            "left": np.swapaxes(a, 0, 1)[::-1]}[side]


def _to_unwarp(side: str, x: float, y: float, w: int, h: int) -> tuple[float, float]:
    """_side_view 좌표 → 편 표지(w×h) 좌표."""
    if side == "bottom":
        return x, y
    if side == "top":
        return x, h - 1 - y
    if side == "right":
        return y, x
    return w - 1 - y, x      # left


def _cover_edge(cover: np.ndarray, side: str) -> tuple[tuple, tuple] | None:
    """편 표지의 한 변 근처에 '표지 경계선 + 그 바깥 무채색 띠'가 있으면 그 선의 두 끝점(편 표지 좌표).
    누운 하드커버를 위에서 찍으면 오리기에 표지 아래 책 배·뒤표지 판이 같이 들어온다 (09-29 『모순』)."""
    import cv2
    v = _side_view(cover, side)
    n, m = v.shape[:2]
    band = max(4, int(n * PAGE_BAND))
    gray = cv2.cvtColor(np.ascontiguousarray(v[n - band:]), cv2.COLOR_RGB2GRAY)
    edges = cv2.Canny(cv2.GaussianBlur(gray, (3, 3), 0), 40, 120)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 360, threshold=int(m * 0.3),
                            minLineLength=int(m * PAGE_MIN_LEN), maxLineGap=int(m * 0.03) + 1)
    if lines is None:
        return None
    best = None
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):   # OpenCV 버전마다 (N,1,4) · (N,4) 로 달라서
        if x1 == x2 or abs((y2 - y1) / (x2 - x1)) > PAGE_MAX_TILT:
            continue
        slope = (y2 - y1) / (x2 - x1)
        y_left, y_right = y1 - slope * x1 + n - band, y1 + slope * (m - 1 - x1) + n - band
        strip = n - max(y_left, y_right)
        if strip < n * PAGE_MIN_STRIP:
            continue
        if best is None or max(y_left, y_right) < max(best):   # 가장 안쪽 선 = 표지 경계
            best = (y_left, y_right)
    if best is None:
        return None
    top = int(min(best)) + 1
    hsv = cv2.cvtColor(np.ascontiguousarray(v[top:]), cv2.COLOR_RGB2HSV)
    if hsv[..., 1].mean() / 255 > PAGE_MAX_SAT:
        return None
    h, w = cover.shape[:2]
    return _to_unwarp(side, 0, best[0], w, h), _to_unwarp(side, m - 1, best[1], w, h)


def trim_page_edges(img: Image.Image, q: np.ndarray) -> np.ndarray:
    """네 모서리 q 를 표지 면만 남게 좁힌다 — 변마다 _cover_edge 가 찾은 선으로 그 변을 옮긴다.
    못 찾으면 그 변은 그대로. 원본 픽셀은 바꾸지 않고 모서리 좌표만 바꾼다."""
    import cv2
    tl, tr, br, bl = q
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    m = cv2.getPerspectiveTransform(q.astype(np.float32), dst)
    cover = cv2.warpPerspective(np.asarray(img), m, (w, h), flags=cv2.INTER_LINEAR,
                                borderMode=cv2.BORDER_REPLICATE)
    inv = np.linalg.inv(m)

    def back(pt):
        x, y, z = inv @ np.array([pt[0], pt[1], 1.0])
        return np.array([x / z, y / z], np.float32)

    # 편 표지 좌표에서 네 변을 직선(두 점)으로 두고, 찾은 변만 바꾼 뒤 이웃 변과의 교점이 새 모서리
    sides = {"top": ((0, 0), (w - 1, 0)), "right": ((w - 1, 0), (w - 1, h - 1)),
             "bottom": ((0, h - 1), (w - 1, h - 1)), "left": ((0, 0), (0, h - 1))}
    moved = False
    for side in sides:
        edge = _cover_edge(cover, side)
        if edge is not None:
            sides[side] = edge
            moved = True
            logger.info(f"표지 {side} 쪽 책 배·옆면 띠를 뺌")
    if not moved:
        return q

    def cross(a, b):
        (x1, y1), (x2, y2) = a
        (x3, y3), (x4, y4) = b
        d = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        px = ((x1 * y2 - y1 * x2) * (x3 - x4) - (x1 - x2) * (x3 * y4 - y3 * x4)) / d
        py = ((x1 * y2 - y1 * x2) * (y3 - y4) - (y1 - y2) * (x3 * y4 - y3 * x4)) / d
        return px, py

    corners = [cross(sides["top"], sides["left"]), cross(sides["top"], sides["right"]),
               cross(sides["bottom"], sides["right"]), cross(sides["bottom"], sides["left"])]
    return np.array([back(c) for c in corners], np.float32)


def _unwarp(img: Image.Image, q: np.ndarray) -> Image.Image:
    """좌상·우상·우하·좌하 네 점 → 정면으로 편 표지. 변 길이는 마주 보는 두 변 중 긴 쪽."""
    import cv2
    tl, tr, br, bl = q
    w = int(max(np.linalg.norm(tr - tl), np.linalg.norm(br - bl)))
    h = int(max(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)))
    dst = np.array([[0, 0], [w - 1, 0], [w - 1, h - 1], [0, h - 1]], np.float32)
    m = cv2.getPerspectiveTransform(q, dst)
    return Image.fromarray(cv2.warpPerspective(np.asarray(img), m, (w, h), flags=cv2.INTER_CUBIC,
                                               borderMode=cv2.BORDER_REPLICATE))


def compose_flat(image_bytes: bytes, bg_color: tuple, item_box: dict | None = None) -> bytes:
    """책·음반처럼 납작한 인쇄물 — 표지 네 모서리를 찾아 정면으로 펴고(원근 보정) 배경 위에
    살짝 띄운 그림자와 함께 놓는다 (쇼핑몰 표지 컷처럼). 표지 픽셀은 원근 보정·크기 조정 외에는
    손대지 않는다. 가로세로 비는 모서리 길이 그대로 — 한 장으로 실제 비를 추정하는 방법(Zhang–He)은
    잘리거나 줄인 사진에서 주점 가정이 깨져 오히려 틀렸다 (09-27 책 2권).
    네 모서리를 못 찾거나 펴다가 실패하면 일반 배경 교체(compose)로 — 원본 그대로가 아니라."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    alpha = original_alpha(image_bytes, img)
    try:
        q = find_cover(alpha, item_box)
        cover = None if q is None else _unwarp(img, trim_page_edges(img, q))
    except Exception as e:
        logger.warning(f"표지 펴기 실패: {e}")
        cover = None
    if cover is None:
        logger.info("표지를 펴지 못함 → 일반 배경 교체")
        return compose(image_bytes, bg_color, item_box, alpha=alpha, flat=True)
    w, h = cover.size

    room = CANVAS * (1 - 2 * FLAT_MARGIN)
    scale = min(room / w, room / h, MAX_UPSCALE)
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    cover = cover.resize(size, Image.LANCZOS)
    x = (CANVAS - size[0]) // 2
    y = min((CANVAS - size[1]) // 2 + int(CANVAS * FLAT_DROP), CANVAS - size[1] - int(CANVAS * 0.04))
    canvas = studio_canvas(bg_color)
    dark = Image.new("RGB", (CANVAS, CANVAS), tuple(int(c * 0.55) for c in bg_color))
    # 넓고 옅은 그림자(아래로 조금) + 아랫변에 붙은 짙은 접지 그림자 — 표지가 바닥에 서 있는 컷
    off = max(6, size[0] // 50)
    ambient = Image.new("L", (CANVAS, CANVAS), 0)
    ambient.paste(70, (x + off // 2, y + off, x + size[0] + off // 2, y + size[1] + off))
    ambient = ambient.filter(ImageFilter.GaussianBlur(off * 2.5))
    canvas = Image.composite(dark, canvas, ambient)
    contact = Image.new("L", (CANVAS, CANVAS), 0)
    ch = max(6, size[1] // 40)
    contact.paste(140, (x + ch, y + size[1] - ch, x + size[0] - ch, y + size[1] + ch // 2))
    contact = contact.filter(ImageFilter.GaussianBlur(ch))
    canvas = Image.composite(dark, canvas, contact)
    canvas.paste(cover, (x, y))
    buf = io.BytesIO()
    canvas.save(buf, format="JPEG", quality=95)
    return buf.getvalue()


def compose(image_bytes: bytes, bg_color: tuple, item_box: dict | None = None,
            alpha: np.ndarray | None = None, *, flat: bool = False,
            drop: list[dict] | None = None, keep: list[dict] | None = None) -> bytes:
    """원본 → 물건만 오려 CANVAS 정사각 배경 가운데에 놓고 바닥 그림자를 깐 JPEG.
    물건 픽셀은 크기 조정(리샘플링) 외에는 손대지 않는다. drop = 팔지 않는다고 고른 물건 박스 (지운다)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if alpha is None:
        alpha = original_alpha(image_bytes, img)
    alpha = drop_boxes(alpha, drop, keep)
    item, mask = _item_and_mask(img, alpha, item_box, flat=flat)

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
