"""정답 구도 추천 — 판매자 대표 사진과 정답 사진을 CLIP 이미지 임베딩으로 대 본다 (10-11, 로컬 · 무료).

"무슨 물건이냐"만 맞춘다. 재료(필요한 면 · 구성품)가 다 있나는 AI 사진 검토 · 구도 검사가 본다.
시험 (게시글 11개 × 정답 14개, 대표 사진 한 장): 정답이 11/11 1등. 정답 1등 점수는 0.58~0.89,
2등은 1등보다 0.05~0.27 낮았다 → 1등이 MIN 보다 낮으면 추천 없음, 2 · 3등은 1등과 GAP 안일 때만.
"""
import io
import logging
import threading
from collections import OrderedDict

import numpy as np
from PIL import Image

from app.core.config import settings
from app.services import style_refs

logger = logging.getLogger("carret.ref_matcher")

_MODEL_NAME = "openai/clip-vit-base-patch32"
_model = None
_processor = None
_lock = threading.Lock()
_refs: dict = {}                     # {"key": 정답 목록 키, "files": [...], "vecs": ndarray}
_photo_cache: OrderedDict = OrderedDict()   # (file_id, 정답 키) → 추천
_PHOTO_CACHE_MAX = 256


def _load():
    global _model, _processor
    if _model is None:
        with _lock:
            if _model is None:
                from transformers import CLIPModel, CLIPProcessor
                _processor = CLIPProcessor.from_pretrained(_MODEL_NAME)
                _model = CLIPModel.from_pretrained(_MODEL_NAME)
                _model.eval()
    return _model, _processor


def _embed(images: list[bytes]) -> np.ndarray:
    import torch
    model, processor = _load()
    ims = [Image.open(io.BytesIO(b)).convert("RGB") for b in images]
    with torch.no_grad():
        out = model.get_image_features(**processor(images=ims, return_tensors="pt"))
    vec = (out if torch.is_tensor(out) else out.pooler_output).numpy()
    return vec / np.linalg.norm(vec, axis=1, keepdims=True)


def _ref_vectors() -> tuple[tuple, list[str], np.ndarray | None]:
    """정답 사진 벡터 — 정답 목록(파일 · 이름)이 바뀔 때만 다시 만든다."""
    entries = [e for e in style_refs.load() if e.get("file")]
    key = tuple(e["file"] for e in entries)
    if _refs.get("key") != key:
        files, images = [], []
        for e in entries:
            b = style_refs.answer_photo(e["file"])
            if b:
                files.append(e["file"])
                images.append(b)
        _refs.update(key=key, files=files, vecs=_embed(images) if images else None)
    return key, _refs["files"], _refs["vecs"]


def pick(scores: list[tuple[str, float]]) -> list[dict]:
    """점수 [(정답, 유사도)] → 추천 최대 3개. 1등이 MIN 미만이면 없음, 2 · 3등은 1등과 GAP 안 · MIN 이상일 때만."""
    ranked = sorted(scores, key=lambda x: -x[1])
    if not ranked or ranked[0][1] < settings.ref_suggest_min:
        return []
    top = ranked[0][1]
    out = [r for r in ranked[:3] if r[1] >= settings.ref_suggest_min and top - r[1] <= settings.ref_suggest_gap]
    return [{"file": f, "score": round(s, 3)} for f, s in out]


def suggest(file_id: str, image: bytes) -> list[dict]:
    """대표 사진 한 장 → 추천 정답 [{file, score}] (좋은 것 먼저). 꺼져 있거나 실패하면 [] (추천 없이 화면은 그대로)."""
    if not settings.ref_suggest or settings.pipeline_mode != "real":
        return []
    try:
        key, files, vecs = _ref_vectors()
        if vecs is None:
            return []
        ck = (file_id, key)
        if ck in _photo_cache:
            _photo_cache.move_to_end(ck)
            return _photo_cache[ck]
        sims = (_embed([image]) @ vecs.T)[0]
        out = pick(list(zip(files, sims.tolist())))
        _photo_cache[ck] = out
        while len(_photo_cache) > _PHOTO_CACHE_MAX:
            _photo_cache.popitem(last=False)
        return out
    except Exception as e:
        logger.warning(f"[ref_matcher] 추천 실패 — 추천 없이: {type(e).__name__}: {e}")
        return []
