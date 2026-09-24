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

def _norm(t: str) -> str:
    return " ".join(t.lower().split())


def text_match(before: list[str], after: list[str], added_below: float = 0.6) -> dict:
    """줄 단위, 순서 무관 텍스트 비교.

    text_recall 은 줄을 이어붙인 문자열 하나끼리 비교해서 읽는 순서만 달라도
    (예: 날짜 '29' 가 목록 끝으로 감) 점수가 깎인다. 여기서는 원본 줄마다
    결과에서 가장 비슷한 줄을 찾아 그 유사도를 평균낸다.
      recall  : 원본 줄들이 결과에 얼마나 그대로 남았나 (0-1)
      changed : 원본 줄 중 결과에서 1.0 으로 못 찾은 것 [(원본, 가장 비슷한 결과, 점수)]
      added   : 결과 줄 중 원본 어디와도 added_below 미만인 것 (새로 생긴 글자)"""
    b = [_norm(t) for t in before if _norm(t)]
    a = [_norm(t) for t in after if _norm(t)]

    def best(x, pool):
        if not pool:
            return None, 0.0
        return max(((y, difflib.SequenceMatcher(None, x, y).ratio()) for y in pool),
                   key=lambda p: p[1])

    scores, changed = [], []
    for x in b:
        y, r = best(x, a)
        scores.append(r)
        if r < 1.0:
            changed.append({"before": x, "after": y, "score": round(r, 3)})
    added = [y for y in a if best(y, b)[1] < added_below]
    recall = sum(scores) / len(scores) if scores else 1.0
    return {"recall": round(recall, 3), "changed": changed, "added": added}


def wear_ratio(checks) -> float:
    return (sum(c["preserved"] for c in checks) / len(checks)) if checks else 1.0