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
    """보고용 표기: 소문자 + 공백 하나로."""
    return " ".join(t.lower().split())


def _key(t: str) -> str:
    """비교용 키: 소문자 + 글자·숫자만 (띄어쓰기·구두점 무시).
    VLM 이 같은 글자를 "H.M"/"H-M", "3060"/"3:060", "OFFICIAL"/"official" 처럼 매번 조금씩
    다르게 읽는다 — 이 차이로 멀쩡한 생성본이 반려되던 것 (2026-09-27 폰 사진 6장)."""
    return "".join(ch for ch in t.lower() if ch.isalnum())


SPLIT_MAX_PARTS = 3


def _split_match(x: str, keys: list[str]) -> bool:
    """원본 한 줄(x)을 결과가 2~3 줄로 쪼개 읽었나 — 결과 줄 몇 개를 (어떤 순서로든) 그대로 이어
    붙이면 x 와 **정확히** 같을 때만. 부분 문자열 포함("3060" in "13060")은 글자가 바뀐 것도
    통과시키므로 쓰지 않는다. 숫자가 섞인 줄도 이 방식이면 자리 하나만 달라도 불일치."""
    from itertools import permutations
    parts = [k for k in keys if k and k in x]   # x 의 조각이 될 수 있는 줄만
    for n in range(2, min(SPLIT_MAX_PARTS, len(parts)) + 1):
        if any("".join(p) == x for p in permutations(parts, n)):
            return True
    return False


def _fragment_of(y: str, keys: list[str]) -> bool:
    """결과 줄 y 가 원본 어느 한 줄의 조각인가 (쪼개 읽은 것이지 새 글자가 아님). 2자 이상만 —
    한 글자는 어디에든 들어 있어 새로 생긴 글자도 조각으로 보인다."""
    return len(y) >= 2 and any(y in k and y != k for k in keys)


def text_match(before: list[str], after: list[str], added_below: float = 0.6) -> dict:
    """줄 단위, 순서 무관 텍스트 비교 (띄어쓰기·구두점·대소문자 무시 — _key 로 비교).

    text_recall 은 줄을 이어붙인 문자열 하나끼리 비교해서 읽는 순서만 달라도
    (예: 날짜 '29' 가 목록 끝으로 감) 점수가 깎인다. 여기서는 원본 줄마다
    결과에서 가장 비슷한 줄을 찾아 그 유사도를 평균낸다. 한 줄을 둘·셋으로 쪼개 읽은
    경우("00 3060" → "00" + "3060", 순서 무관)는 조각을 이으면 정확히 같을 때만 그대로 남은 것으로 본다.
      recall  : 원본 줄들이 결과에 얼마나 그대로 남았나 (0-1)
      changed : 원본 줄 중 결과에서 1.0 으로 못 찾은 것 [(원본, 가장 비슷한 결과, 점수)]
      added   : 결과 줄 중 원본 어디와도 added_below 미만이고 원본 한 줄의 조각(2자 이상)도 아닌 것"""
    b = [(_norm(t), _key(t)) for t in before if _key(t)]
    a = [(_norm(t), _key(t)) for t in after if _key(t)]

    def best(x, pool):
        if not pool:
            return None, 0.0
        return max(((y, difflib.SequenceMatcher(None, x, y[1]).ratio()) for y in pool),
                   key=lambda p: p[1])

    scores, changed = [], []
    for shown, x in b:
        y, r = best(x, a)
        if r < 1.0 and _split_match(x, [k for _, k in a]):
            y, r = None, 1.0
        scores.append(r)
        if r < 1.0:
            changed.append({"before": shown, "after": y[0] if y else None, "score": round(r, 3)})
    added = [shown for shown, y in a
             if best(y, b)[1] < added_below and not _fragment_of(y, [k for _, k in b])]
    recall = sum(scores) / len(scores) if scores else 1.0
    return {"recall": round(recall, 3), "changed": changed, "added": added}


def wear_ratio(checks) -> float:
    return (sum(c["preserved"] for c in checks) / len(checks)) if checks else 1.0