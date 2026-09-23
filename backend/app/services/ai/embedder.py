"""DINOv2 임베딩 기반 구조적 유사도 — VLM judge와 별개의 로컬 벡터 점수.

VLM judge(fidelity/realism/trust)는 "판단"이라 프롬프트/모델 버전에 따라
흔들릴 수 있다. DINOv2 코사인 유사도는 API 호출 없이 로컬 모델 하나로 고정된
축을 재는 값이라 상대적으로 안정적이다 — judge를 대체하지 않고 보완하는 신호.

모델은 모듈 전역에 lazy 싱글톤으로 캐시한다 (첫 호출에서만 로드, ~수백ms~수초).
"""
import io
import threading

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


def cosine_similarity(orig: bytes, result: bytes) -> float:
    """원본 vs 결과 DINOv2 임베딩 코사인 유사도 (구조/의미 보존 정도, 대체로 0~1)."""
    with observe("dino_similarity", as_type="embedding",
                  model=_MODEL_NAME) as obs:
        sim = float(np.dot(embed(orig), embed(result)))
        if obs is not None:
            obs.update(output={"cosine_similarity": sim})
        return sim
