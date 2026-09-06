"""앵커 평가 로직 — CLI 와 API 의 단일 진실원."""
from app.services import detector, storage
from pathlib import Path

IMG_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def _image_map(directory: Path) -> dict[str, Path]:
    """stem → 경로 맵 (확장자 무관, 대소문자 무관)."""
    return {p.stem: p for p in sorted(directory.iterdir())
            if p.suffix.lower() in IMG_EXTS}


def find_pairs() -> list[str]:
    """dataset 페어 이름 목록."""
    imgs   = _image_map(storage.BASE / "dataset" / "img")
    afters = _image_map(storage.BASE / "dataset" / "after")
    return sorted(set(imgs) & set(afters))      # ⭐ 교집합 = 페어


def pair_of(name: str):
    """dataset 에서 (원본, 결과) 페어 찾기."""
    imgs   = _image_map(storage.BASE / "dataset" / "img")
    afters = _image_map(storage.BASE / "dataset" / "after")
    if name in imgs and name in afters:
        return imgs[name], afters[name]
    return None


def eval_pair(name: str) -> dict | None:
    """1페어 앵커 테스트: 양쪽 독립 검출 + 매칭."""
    pair = pair_of(name)
    if pair is None:
        return None
    img, after = pair
    oa = detector.detect_defects(img.read_bytes())
    ra = detector.detect_with_boxes(after.read_bytes())
    m = detector.match_anchors(oa, ra)
    rec = len(m["matched"]) / len(oa) if oa else 1.0
    pre = len(m["matched"]) / len(ra) if ra else 1.0
    return {"name": name, "orig": len(oa), "after": len(ra),
            "recall": round(rec, 2), "precision": round(pre, 2),
            "matched": [{"orig": o, "result": r} for o, r in m["matched"]],
            "missed": m["missed"], "new": m["new"]}


def eval_all() -> list[dict]:
    """전 페어 요약표."""
    return [row for name in find_pairs()
            if (row := eval_pair(name))]

def eval_bytes(orig: bytes, result: bytes, name: str = "") -> dict:
    """바이트 1쌍 평가 — detect×2 + 매칭 + 지표."""
    oa = detector.detect_defects(orig)
    ra = detector.detect_with_boxes(result)
    m = detector.match_anchors(oa, ra)
    rec = len(m["matched"]) / len(oa) if oa else 1.0
    pre = len(m["matched"]) / len(ra) if ra else 1.0
    return {"name": name, "orig": len(oa), "after": len(ra),
            "recall": round(rec, 2), "precision": round(pre, 2),
            "matched": [{"orig": o, "result": r} for o, r in m["matched"]],
            "missed": m["missed"], "new": m["new"]}


def eval_pair(name: str) -> dict | None:
    pair = pair_of(name)
    if pair is None:
        return None
    img, after = pair
    return eval_bytes(img.read_bytes(), after.read_bytes(), name=name)