"""하자 검출기.

detect_defects   : 원본 → 의미 앵커 (what/where)
verify_and_locate: 결과 → 보존 여부 + 결과 좌표 (말풍선용)
"""
import json

from google import genai
from google.genai import types

from app.core.config import settings
from app.prompts import detector as P

# detector.py — 순서가 아니라 "키" 로만 읽기
def _box(d: dict) -> dict:
    return {"x1": int(d["x1"]), "y1": int(d["y1"]),
            "x2": int(d["x2"]), "y2": int(d["y2"])}

def _call(image_bytes: bytes, prompt: str) -> dict:
    """공통 VLM 호출 (temp 0 + JSON 모드)."""
    client = genai.Client(api_key=settings.VLM_KEY)
    resp = client.models.generate_content(
        model=settings.VLM_MODEL,
        contents=[
            types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
            prompt,
        ],
        config=types.GenerateContentConfig(
            temperature=0,
            response_mime_type="application/json",
        ),
    )
    try:
        return json.loads(resp.text)
    except json.JSONDecodeError:
        print(f"[detector] JSON 파싱 실패: {resp.text[:200]}")
        return {}


def detect_defects(image_bytes: bytes) -> list[dict]:
    """원본에서 의미 앵커 추출 (좌표 없음)."""
    data = _call(image_bytes, P.DETECT_PROMPT)
    return [d for d in data.get("defects", []) if _valid_anchor(d)]


def verify_and_locate(image_bytes: bytes, anchors: list[dict]) -> list[dict]:
    prompt = P.VERIFY_PROMPT.format(
        anchors=[(a["what"], a["where"]) for a in anchors],
    )
    data = _call(image_bytes, prompt)
    checks = [c for c in data.get("checks", []) if _valid_check(c)]
    # ⭐ 보존+좌표 있는 것만 _box 로 정제
    return [{**c, **_box(c)} if c.get("preserved") and _has_box(c) else c
            for c in checks]

def all_preserved(checks: list[dict]) -> bool:
    """게이트용: 하자 전부 살아있는가."""
    return all(c.get("preserved") for c in checks) if checks else True


def bubbles(checks: list[dict]) -> list[dict]:
    """말풍선용: 보존된 하자만, 좌표 있는 것만."""
    return [c for c in checks if c.get("preserved") and _has_box(c)]


# ---- 검증 헬퍼 ----
def _valid_anchor(d: dict) -> bool:
    return bool(d.get("what")) and bool(d.get("where"))


def _valid_check(c: dict) -> bool:
    if not c.get("what"):
        return False
    if not c.get("preserved"):
        return True            # 보존 안 됨 = 좌표 불필요, 게이트엔 필요
    return _has_box(c)


def _has_box(c: dict) -> bool:
    return all(
        isinstance(c.get(k), int) and 0 <= c[k] <= 1000
        for k in ("x1", "y1", "x2", "y2")
    )
def detect_with_boxes(image_bytes: bytes) -> list[dict]:
    data = _call(image_bytes, P.DETECT_BOX_PROMPT)
    return [{**d, **_box(d)} for d in data.get("defects", [])
            if _valid_anchor(d) and _has_box(d)]


def match_anchors(orig: list[dict], result: list[dict]) -> dict:
    """원본 vs 결과 의미 매칭 → matched / missed / new."""
    if not orig or not result:
        return {"matched": [], "missed": orig, "new": result}

    client = genai.Client(api_key=settings.VLM_KEY)
    resp = client.models.generate_content(
        model=settings.VLM_MODEL,
        contents=[P.MATCH_PROMPT.format(orig=orig, result=result)],
        config=types.GenerateContentConfig(
            temperature=0, response_mime_type="application/json"),
    )
    try:
        matches = json.loads(resp.text).get("matches", [])
    except json.JSONDecodeError:
        matches = []

    matched, new, hit = [], [], set()
    for j, r in enumerate(result):
        i = matches[j] if j < len(matches) else -1
        if isinstance(i, int) and 0 <= i < len(orig):
            matched.append((orig[i], r))
            hit.add(i)
        else:
            new.append(r)
    missed = [o for k, o in enumerate(orig) if k not in hit]
    return {"matched": matched, "missed": missed, "new": new}