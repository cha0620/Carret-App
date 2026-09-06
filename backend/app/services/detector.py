"""하자 검출기 - VLM 의 손.

라이브 경로:
  classify         : 이미지 → {item, considered} (루브릭 수립)
  detect_defects   : 원본 → 의미 앵커 (what/where)
  verify_and_locate: 결과 → 보존 여부 + 결과 좌표 (말풍선용)

측정 경로 (eval/dev 전용 — 파이프라인과 분리, 절대 삭제 금지):
  detect_with_boxes: 좌표付き 검출 (recall/precision 계측)
  match_anchors    : 원본 vs 결과 의미 매칭 → matched/missed/new

공유: bubbles / all_preserved / _box / _call / 검증 헬퍼
"""
import json

from google import genai
from google.genai import types

from app.core.config import settings
from app.prompts import detector as P


# ── 공통 ─────────────────────────────────────────
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


def _box(d: dict) -> dict:
    """좌표 정규화: 항상 x1<x2, y1<y2 (전치 회귀 방지)."""
    x1, x2 = sorted((int(d["x1"]), int(d["x2"])))
    y1, y2 = sorted((int(d["y1"]), int(d["y2"])))
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


# ── 라이브 경로 ──────────────────────────────────
def classify(image_bytes: bytes) -> dict:
    """물건 식별 + 루브릭 수립 (실패 시 개방형 폴백)."""
    try:
        out = _call(image_bytes, P.CLASSIFY_PROMPT)
        return {
            "item": str(out.get("item", "object")).strip() or "object",
            "considered": [str(c).strip()
                           for c in out.get("considered", [])][:8],
        }
    except Exception as e:
        print(f"[classify] 실패(무시): {e}")
        return {"item": "object", "considered": []}


def detect_defects(image_bytes: bytes, item: str, considered: list) -> list:
    data = _call(image_bytes, P.detect_prompt(item, considered))
    anchors = []
    for d in data.get("defects", []):
        cat = str(d.get("category", "other")).strip()
        if cat not in P.CATEGORIES:      # 목록 밖이면 other 로
            cat = "other"
        anchors.append({
            "category": cat,
            "what":  str(d.get("what", "")).strip(),
            "where": str(d.get("where", "")).strip(),
        })
    return anchors


def verify_and_locate(image_bytes, anchors,
                      item="object", considered=None) -> list:
    """결과 → 보존 여부 + 결과 좌표."""
    considered = considered or []
    data = _call(image_bytes, P.verify_prompt(anchors, item, considered))
    checks = [c for c in data.get("checks", []) if _valid_check(c)]
    # 보존+좌표 있는 것만 _box 로 정제
    return [{**c, **_box(c)} if c.get("preserved") and _has_box(c) else c
            for c in checks]


def all_preserved(checks: list) -> bool:
    """게이트용: 하자 전부 살아있는가 (빈 리스트 = 통과)."""
    return all(c.get("preserved") for c in checks) if checks else True


def bubbles(checks: list) -> list:
    out = []
    for c in checks:
        if c.get("preserved") and _has_box(c):
            label = c["what"]
            if label.startswith(("logo:", "text:")):
                label = "🏷️ " + label.split(":", 1)[1]   # 말풍선용
            out.append({**c, "label": label})
    return out


# ── 측정 경로 (eval/dev 전용) ────────────────────
def detect_with_boxes(image_bytes: bytes) -> list:
    """좌표付き 검출 (recall/precision 계측용)."""
    data = _call(image_bytes, P.DETECT_BOX_PROMPT)
    return [{**d, **_box(d)} for d in data.get("defects", [])
            if _valid_anchor(d) and _has_box(d)]


def match_anchors(orig: list, result: list) -> dict:
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


# ── 검증 헬퍼 ────────────────────────────────────
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