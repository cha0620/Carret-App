"""DINOv2 임베딩 기반 구조적 유사도 — VLM judge와 별개의 로컬 벡터 점수.

VLM judge(fidelity/realism/trust)는 "판단"이라 프롬프트/모델 버전에 따라
흔들릴 수 있다. DINOv2 코사인 유사도는 API 호출 없이 로컬 모델 하나로 고정된
축을 재는 값이라 상대적으로 안정적이다 — judge를 대체하지 않고 보완하는 신호.

모델은 모듈 전역에 lazy 싱글톤으로 캐시한다 (첫 호출에서만 로드, ~수백ms~수초).
"""
import hashlib
import io
import threading
from collections import OrderedDict

import numpy as np
from PIL import Image

from app.core.tracing import observe

_MODEL_NAME = "facebook/dinov2-small"
_model = None
_processor = None
_lock = threading.Lock()


def _load():
    """싱글톤 (double-checked locking) — 동시 요청이 첫 호출을 같이 타도
    (CPU/메모리 비용이 실제로 드는) 모델이 두 번 로드되지 않게."""
    global _model, _processor
    if _model is None:
        with _lock:
            if _model is None:
                from transformers import AutoImageProcessor, AutoModel
                _processor = AutoImageProcessor.from_pretrained(_MODEL_NAME)
                _model = AutoModel.from_pretrained(_MODEL_NAME)
                _model.eval()
    return _model, _processor


def embed(image_bytes: bytes) -> np.ndarray:
    """이미지 1장 → 정규화된 DINOv2 CLS 임베딩 벡터(384차원)."""
    import torch
    model, processor = _load()
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    inputs = processor(images=img, return_tensors="pt")
    with torch.no_grad():
        out = model(**inputs)
    vec = out.last_hidden_state[0, 0, :].numpy()
    return vec / np.linalg.norm(vec)


_ORIG_CACHE: OrderedDict = OrderedDict()
_ORIG_CACHE_MAX = 32   # 변환 1회에 원본 2장(전체·누끼)을 쓴다


def _embed_original(image_bytes: bytes) -> np.ndarray:
    """원본 임베딩 캐시 — 한 변환 안에서 결과만 바뀌고(재생성·합성) 원본은 같아서
    매번 다시 임베딩할 필요가 없다. 키에 embed 함수 자체도 넣는다 (테스트가 embed 를
    바꿔치기했을 때 다른 가짜의 값이 섞이지 않게). 결과 쪽은 매번 달라 캐시하지 않는다."""
    key = (hashlib.sha1(image_bytes).digest(), embed)
    with _lock:
        if key in _ORIG_CACHE:
            _ORIG_CACHE.move_to_end(key)
            return _ORIG_CACHE[key]
    vec = embed(image_bytes)
    if hasattr(vec, "setflags"):
        vec.setflags(write=False)   # 공유 캐시 — 호출부가 제자리 연산으로 오염시키지 못하게
    with _lock:
        _ORIG_CACHE[key] = vec
        while len(_ORIG_CACHE) > _ORIG_CACHE_MAX:
            _ORIG_CACHE.popitem(last=False)
    return vec


PATCH = 14            # DINOv2 패치 크기 (px)
# 비교 대상은 물건 "안쪽" 패치만 — 전부 물건 픽셀이고 이웃 8칸도 그런 패치. 원본(fal)·결과(rembg)는
# 누끼 방식이 달라 윤곽이 어긋나는데, 가장자리 패치를 넣으면 하위 1% 가 흠집이 아니라 누끼 차이를 잰다.
PATCH_PERCENTILE = 1  # 패치 유사도 분포의 하위 몇 % 를 값으로 — 흠집 하나는 물건 패치의 ~1% 라
                      # 5% 면 묻힌다. 최솟값 하나는 너무 흔들린다 (합성 흠집 테스트 2026-09-26)


def _align(a: Image.Image, b: Image.Image, bg: tuple) -> Image.Image:
    """b 를 a 에 맞춰 affine 정렬 (ECC). 생성 결과는 물건 모양·위치가 조금씩 달라서 정렬 없이
    패치를 비교하면 몇 px 어긋남만으로 값이 무너진다. 수렴 실패면 그대로 돌려준다."""
    import cv2
    ga = cv2.cvtColor(np.asarray(a), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    gb = cv2.cvtColor(np.asarray(b), cv2.COLOR_RGB2GRAY).astype(np.float32) / 255
    warp = np.eye(2, 3, dtype=np.float32)
    try:
        _, warp = cv2.findTransformECC(
            ga, gb, warp, cv2.MOTION_AFFINE,
            (cv2.TERM_CRITERIA_EPS | cv2.TERM_CRITERIA_COUNT, 100, 1e-5), None, 5)
    except cv2.error:
        return b
    # 수렴했어도 엉뚱한 변환(크게 확대·뒤집힘·멀리 이동)이면 버린다 — 물건이 정말 달라졌을 때
    # (가드가 제일 필요한 때) 잘 생긴다. isolate 는 크기·위치를 이미 맞춰 두므로 작은 보정만 정상.
    det = float(np.linalg.det(warp[:, :2]))
    if not (0.8 < det < 1.25) or np.abs(warp[:, 2]).max() > 0.1 * max(a.size):
        return b
    out = cv2.warpAffine(np.asarray(b), warp, a.size,
                         flags=cv2.INTER_LINEAR + cv2.WARP_INVERSE_MAP, borderValue=bg)
    return Image.fromarray(out)


def _patch_tokens(img: Image.Image) -> np.ndarray:
    """이미지 → 정규화된 패치 토큰 (rows, cols, dim). 리사이즈·크롭 없이 넣는다 — 기본
    전처리(224 크롭)는 가장자리를 자르고 격자가 16×16 으로 거칠어진다."""
    import torch
    model, processor = _load()
    inputs = processor(images=img, do_resize=False, do_center_crop=False, return_tensors="pt")
    with torch.no_grad():
        out = model(**inputs)
    rows, cols = img.height // PATCH, img.width // PATCH
    tok = out.last_hidden_state[0, 1:, :].numpy()   # CLS 제외
    if tok.shape[0] != rows * cols:   # 전처리가 무시됐거나 register 토큰 모델 — 틀린 격자로 재지 않는다
        raise ValueError(f"패치 토큰 {tok.shape[0]}개 ≠ 격자 {rows}×{cols}")
    tok = tok.reshape(rows, cols, -1)
    return tok / np.linalg.norm(tok, axis=-1, keepdims=True)


def patch_similarity(orig: bytes, result: bytes, bg: tuple = (128, 128, 128)) -> float:
    """누끼 쌍(compositor.isolate — 같은 크기·같은 배치)의 국소 유사도. 결과를 원본에 정렬한 뒤
    원본 물건 패치마다 결과의 같은 자리 주변 3×3 중 가장 닮은 패치와의 코사인을 재고(정렬 오차 흡수), 그 분포의
    하위 PATCH_PERCENTILE % 를 돌려준다. CLS 하나로는 안 보이는 "한 군데만 바뀜"(흠집·글자가
    지워짐)을 보려는 값. bg: isolate 배경색 — 이 색이 섞인 패치와 그 이웃은 뺀다 (물건 안쪽만)."""
    with observe("item_patch_similarity", as_type="embedding", model=_MODEL_NAME) as obs:
        a = Image.open(io.BytesIO(orig)).convert("RGB")
        b = Image.open(io.BytesIO(result)).convert("RGB")
        if a.size != b.size:
            b = b.resize(a.size, Image.LANCZOS)
        w, h = (a.width // PATCH) * PATCH, (a.height // PATCH) * PATCH
        a = a.crop((0, 0, w, h))
        b = _align(a, b.crop((0, 0, w, h)), bg)
        ta, tb = _patch_tokens(a), _patch_tokens(b)
        rows, cols = ta.shape[:2]
        item_px = np.any(np.asarray(a) != np.array(bg, dtype=np.uint8), axis=-1)
        full = item_px.reshape(rows, PATCH, cols, PATCH).all(axis=(1, 3))
        fp = np.pad(full, 1)
        fg = np.ones_like(full)
        for dy in range(3):
            for dx in range(3):
                fg &= fp[dy:dy + rows, dx:dx + cols]
        pad = np.pad(tb, ((1, 1), (1, 1), (0, 0)))
        best = np.full((rows, cols), -1.0)
        for dy in range(3):
            for dx in range(3):
                best = np.maximum(best, np.sum(ta * pad[dy:dy + rows, dx:dx + cols], axis=-1))
        vals = best[fg]
        if vals.size == 0:
            raise ValueError("물건 패치가 없음")
        sim = float(np.percentile(vals, PATCH_PERCENTILE))
        if obs is not None:
            obs.update(output={"patch_similarity": sim, "patches": int(vals.size)})
        return sim


def cosine_similarity(orig: bytes, result: bytes, name: str = "dino_similarity") -> float:
    """원본 vs 결과 DINOv2 임베딩 코사인 유사도 (구조/의미 보존 정도, 대체로 0~1).
    name: Langfuse span 이름 — 이미지 전체와 누끼 비교를 구분해서 보려고."""
    with observe(name, as_type="embedding",
                  model=_MODEL_NAME) as obs:
        sim = float(np.dot(_embed_original(orig), embed(result)))
        if obs is not None:
            obs.update(output={"cosine_similarity": sim})
        return sim
