"""로컬 OCR (EasyOCR) — VLM 과 독립된 두 번째 글자 읽기. ocr_local 가드(soft) 전용.

ocr_match(hard)는 같은 VLM 이 원본·결과를 두 번 읽어 비교해서, 읽기 흔들림(줄 쪼개기·순서)이
그대로 점수에 섞인다. 여기는 결정론적 OCR 이라 같은 글자는 같게 읽는다 — 두 신호가 얼마나
어긋나는지 모아 보고 어느 쪽을 믿을지 정한다 (settings.local_ocr_guard 로 켤 때만).

scripts/run_text_check.py 의 _easyocr_lines 와 같은 읽기 (언어·신뢰도 기준).
"""
import hashlib
import io
import threading
from collections import OrderedDict

from PIL import Image

MIN_CONF = 0.3
BOX_PAD = 30   # 물건 박스 여유 (0-1000 단위 = 3%)
_reader = None
_load_lock = threading.Lock()
_cache_lock = threading.Lock()
_ORIG_CACHE: OrderedDict = OrderedDict()
_ORIG_CACHE_MAX = 16   # 원본은 재생성·재시도마다 같다


def _load():
    global _reader
    if _reader is None:
        with _load_lock:
            if _reader is None:
                import easyocr
                _reader = easyocr.Reader(["ko", "en"], gpu=False, verbose=False)
    return _reader


def read_lines(image_bytes: bytes, box: dict | None = None) -> list[str]:
    """이미지(box 가 있으면 그 영역, 0-1000)의 글자 조각들 (단어 단위, 신뢰도 MIN_CONF 이상)."""
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    if box:
        # VLM 박스가 빡빡하면 가장자리 글자가 잘린다 — 사방 BOX_PAD(0-1000 단위) 여유
        w, h = img.size
        x1, y1 = max(0, box["x1"] - BOX_PAD), max(0, box["y1"] - BOX_PAD)
        x2, y2 = min(1000, box["x2"] + BOX_PAD), min(1000, box["y2"] + BOX_PAD)
        img = img.crop((x1 * w // 1000, y1 * h // 1000, x2 * w // 1000, y2 * h // 1000))
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return [t for _, t, conf in _load().readtext(buf.getvalue()) if conf >= MIN_CONF]


def read_original(image_bytes: bytes, box: dict | None = None) -> list[str]:
    """원본 읽기 캐시 — 한 변환 안에서 결과만 바뀐다."""
    key = (hashlib.sha1(image_bytes).digest(), tuple(sorted((box or {}).items())))
    with _cache_lock:
        if key in _ORIG_CACHE:
            _ORIG_CACHE.move_to_end(key)
            return list(_ORIG_CACHE[key])
    lines = read_lines(image_bytes, box)
    with _cache_lock:
        _ORIG_CACHE[key] = tuple(lines)   # 공유 캐시 — 호출부가 고쳐도 오염되지 않게
        while len(_ORIG_CACHE) > _ORIG_CACHE_MAX:
            _ORIG_CACHE.popitem(last=False)
    return lines
