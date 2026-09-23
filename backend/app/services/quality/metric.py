import difflib, io
import numpy as np
from PIL import Image

def _rgb(b): return Image.open(io.BytesIO(b)).convert("RGB")

def _bbox(mask_bytes):
    a = np.asarray(Image.open(io.BytesIO(mask_bytes)).convert("L"), float) > 128
    ys, xs = np.where(a)
    return (xs.min(), ys.min(), xs.max(), ys.max())

def product_sim(orig, result, orig_mask, result_mask) -> float:
    """상품 영역만 크롭 → 크기 정렬 → 픽셀 보존율"""
    ob = _rgb(orig).crop(_bbox(orig_mask))
    rb = _rgb(result).crop(_bbox(result_mask)).resize(ob.size)
    diff = np.abs(np.asarray(ob, float) - np.asarray(rb, float)).mean() / 255
    return float(1 - diff)

def bg_whiteness(result, result_mask) -> float:
    """스튜디오 프리셋용: 배경이 얼마나 흰가"""
    r = np.asarray(_rgb(result), float)
    m = np.asarray(Image.open(io.BytesIO(result_mask)).convert("L"), float) > 128
    bg = r[~m]
    return float(bg.mean() / 255) if len(bg) else 0.0

def text_recall(expected, transcript) -> float:
    return difflib.SequenceMatcher(
        None, expected.lower(), transcript.lower()).ratio()

def wear_ratio(checks) -> float:
    return (sum(c["preserved"] for c in checks) / len(checks)) if checks else 1.0