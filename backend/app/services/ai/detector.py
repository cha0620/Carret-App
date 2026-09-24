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
from app.core.tracing import gemini_usage as _usage
from app.core.tracing import observe
from app.core.vlm import thinking
from app import prompts as P


# ── 공통 ─────────────────────────────────────────
def _call(image_bytes: bytes, prompt: str, name: str = "vlm_call") -> dict:
    """공통 VLM 호출 (temp 0 + JSON 모드)."""
    client = genai.Client(api_key=settings.VLM_KEY)
    with observe(name, as_type="generation", model=settings.VLM_MODEL,
                 input=prompt) as obs:
        resp = client.models.generate_content(
            model=settings.VLM_MODEL,
            contents=[
                types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                thinking_config=thinking(name),
            ),
        )
        try:
            data = json.loads(resp.text)
        except json.JSONDecodeError:
            print(f"[detector] JSON 파싱 실패: {resp.text[:200]}")
            data = {}
        if obs is not None:
            obs.update(output=data, usage_details=_usage(resp))
        return data


def _from_box_2d(c: dict) -> dict:
    """Gemini 기본 박스 포맷 box_2d=[ymin, xmin, ymax, xmax] (0-1000) → x1/y1/x2/y2.

    Gemini 는 이 순서(y 먼저)로 학습돼 있어서, x1/y1/x2/y2 키로 달라고 하면
    가끔 y 값을 x 자리에 채워 넣는다(가로세로 전치) — 그래서 모델에게는
    익숙한 box_2d 로 받고 변환은 코드가 한다. box_2d 가 없으면(옛 프롬프트
    버전 응답) x1.. 키를 그대로 둔다."""
    b = c.get("box_2d")
    if not (isinstance(b, (list, tuple)) and len(b) == 4
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in b)):
        return c
    ymin, xmin, ymax, xmax = (int(round(v)) for v in b)
    out = {k: v for k, v in c.items() if k != "box_2d"}
    return {**out, "x1": xmin, "y1": ymin, "x2": xmax, "y2": ymax}


def _box(d: dict) -> dict:
    """좌표 정규화: 항상 x1<x2, y1<y2 (전치 회귀 방지)."""
    x1, x2 = sorted((int(d["x1"]), int(d["x2"])))
    y1, y2 = sorted((int(d["y1"]), int(d["y2"])))
    return {"x1": x1, "y1": y1, "x2": x2, "y2": y2}


# ── 라이브 경로 ──────────────────────────────────
def classify(image_bytes: bytes) -> dict:
    """물건 식별 + 루브릭 수립 (실패 시 개방형 폴백)."""
    try:
        out = _call(image_bytes, P.classify_prompt(), "classify")
        return {
            "item": str(out.get("item", "object")).strip() or "object",
            "considered": [str(c).strip()
                           for c in out.get("considered", [])][:8],
        }
    except Exception as e:
        print(f"[classify] 실패(무시): {e}")
        return {"item": "object", "considered": []}


def detect_defects(image_bytes: bytes, item: str = "object",
                    considered: list | None = None) -> list:
    data = _call(image_bytes, P.detect_prompt(item, considered or []), "detect")
    anchors = []
    for d in data.get("defects", []):
        cat = str(d.get("category", "other")).strip()
        if cat not in P.VALID_CATEGORIES:      # 목록 밖이면 other 로
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
    data = _call(image_bytes, P.verify_prompt(anchors, item, considered), "verify")
    checks = [c for c in map(_from_box_2d, data.get("checks", [])) if _valid_check(c)]
    # 보존+좌표 있는 것만 _box 로 정제
    return [{**c, **_box(c)} if c.get("preserved") and _has_box(c) else c
            for c in checks]


def all_preserved(checks: list) -> bool:
    """게이트용: 하자 전부 살아있는가 (빈 리스트 = 통과)."""
    return all(c.get("preserved") for c in checks) if checks else True


def read_item_text(image_bytes: bytes, item: str = "object") -> dict:
    """물건 "위에" 있는 글자만 읽는다 (배경·옷소매·소품 글자 제외, 자동 교정 금지).

    반환: {"item_box": {x1..} | None, "texts": [{"text": str, x1..y2(있으면)}]}
    item_box 는 OCR 을 물건 영역으로만 제한할 때 쓴다."""
    data = _call(image_bytes, P.item_text_prompt(item), "item_text")
    item_box = _from_box_2d({"box_2d": data.get("item_box_2d")})
    item_box = _box(item_box) if _has_box(item_box) else None
    texts = []
    for t in data.get("texts", []):
        if not isinstance(t, dict):
            continue
        text = str(t.get("text", "")).strip()
        if not text:
            continue
        t = _from_box_2d(t)
        texts.append({"text": text, **(_box(t) if _has_box(t) else {})})
    return {"item_box": item_box, "texts": texts}


def check_photo(image_bytes: bytes) -> dict:
    """생성 결과가 '제대로 된 사진'인가 — 구도가 잘렸거나 자막/텍스트로 상품이
    가려졌으면 invalid (재생성 게이트). 실패 시 개방형 폴백(valid=True) — VLM
    장애로 정상 생성물까지 재생성 루프에 태우지 않기 위함."""
    try:
        data = _call(image_bytes, P.check_photo_prompt(), "check_photo")
        return {
            "valid": _as_bool(data.get("valid", True)),
            "reason": str(data.get("reason", "")).strip(),
        }
    except Exception as e:
        print(f"[check_photo] 실패(무시): {e}")
        return {"valid": True, "reason": ""}


def _as_bool(v) -> bool:
    """VLM이 JSON 모드에서도 valid 를 "false" 문자열로 줄 수 있어 bool() 단순
    캐스팅은 위험 — 문자열이면 값 자체를 보고 판단."""
    if isinstance(v, str):
        return v.strip().lower() not in ("false", "0", "no", "")
    return bool(v)


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
    data = _call(image_bytes, P.detect_box_prompt(), "detect_with_boxes")
    return [{**d, **_box(d)} for d in data.get("defects", [])
            if _valid_anchor(d) and _has_box(d)]


def match_anchors(orig: list, result: list) -> dict:
    """원본 vs 결과 의미 매칭 → matched / missed / new."""
    if not orig or not result:
        return {"matched": [], "missed": orig, "new": result}

    prompt = P.match_prompt(orig, result)
    client = genai.Client(api_key=settings.VLM_KEY)
    with observe("match", as_type="generation", model=settings.VLM_MODEL,
                 input=prompt) as obs:
        resp = client.models.generate_content(
            model=settings.VLM_MODEL,
            contents=[prompt],
            config=types.GenerateContentConfig(
                temperature=0, response_mime_type="application/json",
                thinking_config=thinking("match")),
        )
        try:
            matches = json.loads(resp.text).get("matches", [])
        except json.JSONDecodeError:
            matches = []
        if obs is not None:
            obs.update(output={"matches": matches}, usage_details=_usage(resp))

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
    """0-1000 범위 정수 4개 + 넓이 있음 (x1==x2 나 y1==y2 인 퇴화 박스는 말풍선/가드 크롭에 못 씀)."""
    if not all(isinstance(c.get(k), int) and 0 <= c[k] <= 1000
               for k in ("x1", "y1", "x2", "y2")):
        return False
    return c["x1"] != c["x2"] and c["y1"] != c["y2"]