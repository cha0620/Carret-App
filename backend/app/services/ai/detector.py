"""하자 검출기 - VLM 의 손.

라이브 경로:
  analyze          : 원본 → 물건·아이덴티티 마크·사진 유형·하자 수준·워터마크·글자 수준 (VLM 1회)
  verify_and_locate: 결과 → 보존 여부 + 결과 좌표 (말풍선용)

측정 경로 (eval/dev 전용 — 파이프라인과 분리, 절대 삭제 금지):
  classify / detect_full / detect_defects: 옛 앞단 (하자 앵커 recall·precision eval, run_text_check)
  detect_with_boxes: 좌표付き 검출 (recall/precision 계측)
  match_anchors    : 원본 vs 결과 의미 매칭 → matched/missed/new

공유: bubbles / all_preserved / _box / _call / 검증 헬퍼
"""
import json

from google.genai import types

from app.core.config import settings
from app.core.tracing import gemini_usage as _usage
from app.core.tracing import observe
from app.core.vlm import get_client, image_part, thinking
from app.core.vlm import model as vlm_model
from app import prompts as P
from app.prompts.presets import prompt_safe


# ── 공통 ─────────────────────────────────────────
def _call(image_bytes: bytes, prompt: str, name: str = "vlm_call") -> dict:
    """공통 VLM 호출 (temp 0 + JSON 모드)."""
    client = get_client()
    with observe(name, as_type="generation", model=vlm_model(name),
                 input=prompt) as obs:
        resp = client.models.generate_content(
            model=vlm_model(name),
            contents=[
                image_part(image_bytes, "image/jpeg", name),
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
        # lite 모델은 가끔 객체 하나를 목록으로 감싸 준다 ([{"item": ...}]) — 2026-09-26, 19장 중 1번
        if isinstance(out, list) and out and isinstance(out[0], dict):
            out = out[0]
        return {
            "item": str(out.get("item", "object")).strip() or "object",
            "considered": [str(c).strip()
                           for c in out.get("considered", [])][:8],
        }
    except Exception as e:
        print(f"[classify] 실패(무시): {e}")
        return {"item": "object", "considered": []}


def classify_views(images: list[bytes]) -> dict:
    """여러 장 → 물건 종류 + 사진마다 각도 · 가림 · 흐림 (VLM 1회, 낮은 해상도).

    반환: {"category", "item", "photos": [{"view", "occluded", "blurry", "item_visible"}]} — photos 는
    입력 순서와 같은 길이. 답이 빠진 사진은 view=None (각도를 모름 → 빈 면 계산에서 안 센다).
    호출 자체가 실패하면 예외 (호출부가 "각도를 못 봤다"로 처리)."""
    from app.services import coverage
    client = get_client()
    prompt = P.views_prompt(len(images))
    parts = []
    for i, img in enumerate(images):
        parts += [f"Photo {i}:", image_part(img, "image/jpeg", "views")]
    with observe("views", as_type="generation", model=vlm_model("views"), input=prompt) as obs:
        resp = client.models.generate_content(
            model=vlm_model("views"),
            contents=[*parts, prompt],
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                               thinking_config=thinking("views")),
        )
        data = json.loads(resp.text)
        if obs is not None:
            obs.update(output=data, usage_details=_usage(resp))
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict):
        raise ValueError(f"views: 응답 형식이 다름: {str(data)[:200]}")
    by_index = {}
    for p in data.get("photos") or []:
        idx = p.get("index") if isinstance(p, dict) else None
        if isinstance(idx, int) and not isinstance(idx, bool) and 0 <= idx < len(images):   # true 가 1 이 되지 않게
            by_index.setdefault(p["index"], p)
    photos = []
    for i in range(len(images)):
        p = by_index.get(i, {})
        photos.append({"view": coverage.norm_view(p.get("view")),
                       "occluded": p.get("occluded") is True,
                       "blurry": p.get("blurry") is True,
                       "item_visible": p.get("item_visible") is not False})
    return {"category": coverage.norm_category(data.get("category")),
            "item": _text(data.get("item")) or "object", "photos": photos}


TEXT_LEVELS = ("none", "simple", "dense")
PHOTO_TYPES = ("document", "inside_view", "product")
WEAR_LEVELS = ("none", "light", "heavy")
WATERMARKS = ("none", "background", "on_item")


def _level(data: dict, key: str, allowed: tuple, default: str) -> str:
    """분류 값 하나 정규화 — 없거나 모르는 값이면 default (로그로 남겨 관측되게)."""
    v = str(data.get(key) or "").strip().lower()
    if v not in allowed:
        print(f"[analyze] {key} 없음/모름({data.get(key)!r}) → {default}")
        return default
    return v


def _text(v) -> str:
    """VLM 응답 값 → 프롬프트에 다시 넣어도 되는 문자열. null 은 "" ("None" 문자열이 되면
    빈 값 제외 규칙을 빠져나간다), 개행·따옴표·길이는 prompt_safe 로 — analyze 의 마크는 사진 속
    글자를 거의 그대로 옮긴 것이라 verify 프롬프트·gate_note 에 들어갈 때 지시문처럼 읽히면 안 된다."""
    return "" if v is None else prompt_safe(v)


def analyze(image_bytes: bytes) -> dict:
    """파이프라인 첫 단계 (VLM 1회) — 예전 classify + detect 를 합친 것.

    반환: {"item", "considered", "anchors"(아이덴티티 마크만, category="print"), "item_box",
           "photo_type", "wear_level", "watermark", "text_level", "item_count",
           "objects"(사진 속 물건 [{what, box, for_sale}] — 사용자가 팔 물건을 고른다), "item_texts"}
    하자는 목록으로 뽑지 않고 wear_level 로만 본다 (plan 이 heavy 면 생성 전 배경 교체).
    응답에 marks 목록이 없으면(깨진 JSON → {}) 예외 — 빈 목록을 "지킬 게 없음"으로 읽으면
    검증 없이 통과한다 (detect strict 와 같은 이유). 모르는 분류 값은 보수적인 쪽으로:
    photo_type=product(생성 경로 — 예전 동작. 없는 필드면 로그가 남아 옛 프롬프트가 보인다),
    wear_level=light(생성은 하되 heavy 로 단정하지 않음), watermark=none,
    text_level=simple(글자 읽기 후 생성)."""
    data = _call(image_bytes, P.analyze_prompt(), "analyze")
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]   # 객체 하나를 목록으로 감싸 주는 응답 (classify 에서 본 적 있음)
    if not isinstance(data, dict) or not isinstance(data.get("marks"), list):
        raise ValueError(f"analyze: 응답에 marks 목록 없음: {str(data)[:200]}")
    anchors = [{"category": "print", "what": _text(m.get("what")),
                "where": _text(m.get("where"))}
               for m in data["marks"] if isinstance(m, dict)]
    anchors = [a for a in anchors if a["what"]]
    level = _level(data, "text_level", TEXT_LEVELS, "simple")
    if level == "none" and anchors:
        level = "simple"   # 한 응답 안의 모순 — 마크가 있으면 글자 보호를 끄지 않는다 (detect 와 같음)
    item_box = _from_box_2d({"box_2d": data.get("item_box_2d")})
    considered = data.get("considered") if isinstance(data.get("considered"), list) else []
    return {
        "item": _text(data.get("item") or None) or "object",   # 0·"" 등 거짓 값도 object (예전 동작)
        "considered": [_text(c) for c in considered if _text(c)][:8],
        "anchors": anchors,
        "item_box": _box(item_box) if _has_box(item_box) else None,
        "photo_type": _level(data, "photo_type", PHOTO_TYPES, "product"),
        "wear_level": _level(data, "wear_level", WEAR_LEVELS, "light"),
        "watermark": _level(data, "watermark", WATERMARKS, "none"),
        "text_level": level,
        "item_count": _count(data.get("item_count")),
        "objects": _parse_objects(data.get("objects")),
        # 물건 위 글자 (10-01, 글자 읽기 호출을 합침). 응답에 texts 목록이 없으면(옛 프롬프트) 키를 두지
        # 않는다 — read_text 가 예전처럼 따로 읽는다. 글자 수준이 simple 이 아니면 쓰지 않으니 비운다
        **({"item_texts": _parse_texts(data["texts"]) if level == "simple" else []}
           if isinstance(data.get("texts"), list) else {}),
    }


MAX_OBJECTS = 12


def _parse_objects(raw) -> list[dict]:
    """objects → [{"what", "box", "for_sale"}] — 사용자가 팔 물건을 고르는 목록 (10-03).
    이름이나 박스가 이상한 항목은 뺀다 (고를 수 없는 칸이 화면에 나오지 않게). 없으면 빈 목록."""
    out = []
    for o in raw if isinstance(raw, list) else []:
        if not isinstance(o, dict):
            continue
        what, box = _text(o.get("what")), _from_box_2d({"box_2d": o.get("box_2d")})
        if what and _has_box(box):
            out.append({"what": what, "box": _box(box), "for_sale": o.get("for_sale") is not False})
        if len(out) >= MAX_OBJECTS:
            break
    return out


def _count(v) -> int:
    """item_count → 1 이상 정수. 없거나 이상한 값이면 1 (예전 동작 — 한 개로 본다)."""
    if isinstance(v, bool):
        return 1
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return 1
    return n if n >= 1 else 1


def detect_full(image_bytes: bytes, item: str = "object",
                considered: list | None = None, *, strict: bool = False) -> dict:
    """하자 앵커 + 물건 위 글자 수준 + 물건 위치 (VLM 1회).

    반환: {"anchors": [...], "text_level": "none"|"simple"|"dense", "item_box": {x1..} | None}
    text_level 이 없거나 모르는 값이면 "simple" — 예전 동작(글자 읽기 후 생성)과 같은 쪽으로.
    strict 는 detect_defects 와 같다."""
    data = _call(image_bytes, P.detect_prompt(item, considered or []), "detect")
    if not isinstance(data, dict) or not isinstance(data.get("defects"), list):
        if strict:
            raise ValueError(f"detect: 응답에 defects 목록 없음: {str(data)[:200]}")
        return {"anchors": [], "text_level": "simple", "item_box": None}
    anchors = _anchors(data["defects"])
    level = str(data.get("text_level") or "").strip().lower()
    if level not in TEXT_LEVELS:
        # 프롬프트가 옛 버전이거나 응답이 빠뜨림 — 조용히 simple 이 되면 관측이 안 된다
        print(f"[detect] text_level 없음/모름({data.get('text_level')!r}) → simple")
        level = "simple"
    elif level == "none" and any(a["category"] == "print" for a in anchors):
        # 한 응답 안의 모순 — 글자 앵커가 있으면 글자 보호(TEXT_LOCK·OCR 가드)를 끄지 않는다
        level = "simple"
    item_box = _from_box_2d({"box_2d": data.get("item_box_2d")})
    return {"anchors": anchors, "text_level": level,
            "item_box": _box(item_box) if _has_box(item_box) else None}


def detect_defects(image_bytes: bytes, item: str = "object",
                    considered: list | None = None, *, strict: bool = False) -> list:
    """strict=True (파이프라인): 빈/깨진 응답(JSON 파싱 실패 → {})을 "하자 없음"이
    아니라 실패로 보고 예외 — [] 로 돌려주면 게이트가 검증할 게 없다며 통과시킨다.
    eval/dev 호출부는 기본값(False)으로 예전처럼 [] 를 받는다 (배치가 한 건에 멈추지 않게)."""
    return detect_full(image_bytes, item, considered, strict=strict)["anchors"]


def _anchors(defects: list) -> list:
    anchors = []
    for d in defects:
        cat = str(d.get("category", "other")).strip()
        if cat not in P.VALID_CATEGORIES:      # 목록 밖이면 other 로
            cat = "other"
        anchors.append({
            "category": cat,
            "what":  str(d.get("what", "")).strip(),
            "where": str(d.get("where", "")).strip(),
        })
    return [a for a in anchors if a["what"]]


def verify_and_locate(image_bytes, anchors,
                      item="object", considered=None, *, strict: bool = False,
                      marks: bool = False) -> list:
    """결과 → 보존 여부 + 결과 좌표.
    strict=True (파이프라인 게이트): 깨진 응답({})을 "전부 사라짐"(→ 재생성)이 아니라
    호출 실패로 올린다 — 호출부가 "검증 불가"로 다루게."""
    considered = considered or []
    data = _call(image_bytes, P.verify_prompt(anchors, item, considered, marks=marks), "verify")
    if strict and not (isinstance(data, dict) and isinstance(data.get("checks"), list)):
        raise ValueError(f"verify: 응답에 checks 목록 없음: {str(data)[:200]}")
    raw = data.get("checks", []) if isinstance(data, dict) else []
    return [_clean_check(c) for c in raw if isinstance(c, dict) and _valid_check(c)]


_BOX_KEYS = ("x1", "y1", "x2", "y2")


def _clean_check(c: dict) -> dict:
    """VLM 응답 1개 → 게이트/말풍선이 믿고 쓸 수 있는 모양.
    what 은 문자열, preserved 는 진짜 bool ("false" 문자열이 True 로 새지 않게),
    좌표는 쓸 수 있을 때만 남기고 (정렬), 아니면 지운다 (엉뚱한 박스 방지)."""
    c = _from_box_2d(c)
    out = {k: v for k, v in c.items() if k not in _BOX_KEYS}
    out["what"] = str(c["what"]).strip()
    out["preserved"] = _as_bool(c.get("preserved", False))
    if _has_box(c):
        out.update(_box(c))
    return out


def all_preserved(checks: list, *, expected: int) -> bool:
    """게이트용: 하자 전부 살아있는가.

    expected = 확인을 요청한 앵커 수 (필수 — 빠뜨려서 빈 응답이 통과하지 않게).
    VLM 이 그보다 적게 답하면(빈 응답 포함) 빠진 항목은 확인 못 한 것 = 통과로
    치지 않는다. 앵커가 원래 없으면(expected=0) 빈 리스트 = 통과.
    한계: 개수만 본다 — 앵커 하나를 둘로 쪼개고 다른 하나를 빠뜨리면 못 잡는다."""
    if len(checks) < expected:
        return False
    return all(c.get("preserved") for c in checks) if checks else True


def read_item_text(image_bytes: bytes, item: str = "object", *, strict: bool = False) -> dict:
    """물건 "위에" 있는 글자만 읽는다 (배경·옷소매·소품 글자 제외, 자동 교정 금지).

    반환: {"item_box": {x1..} | None, "texts": [{"text": str, x1..y2(있으면)}]}
    item_box 는 OCR 을 물건 영역으로만 제한할 때 쓴다."""
    data = _call(image_bytes, P.item_text_prompt(item), "item_text")
    if strict and not (isinstance(data, dict) and isinstance(data.get("texts"), list)):
        # 가드용 결과 읽기: 깨진 응답({})을 "글자 전부 사라짐"(recall 0)으로 읽으면 안 된다
        raise ValueError(f"item_text: 응답에 texts 목록 없음: {str(data)[:200]}")
    item_box = _from_box_2d({"box_2d": data.get("item_box_2d")})
    item_box = _box(item_box) if _has_box(item_box) else None
    return {"item_box": item_box, "texts": _parse_texts(data.get("texts"))}


def _parse_texts(raw) -> list[dict]:
    """[{"text", "box_2d"}] → [{"text", x1..y2(있으면)}]. 빈 글자 · 이상한 항목은 뺀다 (null 도 "글자 없음")."""
    texts = []
    for t in raw or []:
        if not isinstance(t, dict):
            continue
        text = str(t.get("text", "")).strip()
        if not text:
            continue
        t = _from_box_2d(t)
        texts.append({"text": text, **(_box(t) if _has_box(t) else {})})
    return texts


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
    client = get_client()
    with observe("match", as_type="generation", model=vlm_model("match"),
                 input=prompt) as obs:
        resp = client.models.generate_content(
            model=vlm_model("match"),
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
    """what 만 있으면 게이트 계산에 남긴다. 좌표는 말풍선용이라 bubbles() 가 따로
    거른다 — 보존됐는데 좌표만 빠진 항목을 여기서 버리면 앵커 수보다 답이 적어져
    all_preserved(expected=) 가 멀쩡한 결과를 실패로 판정한다."""
    return bool(str(c.get("what") or "").strip())


def _has_box(c: dict) -> bool:
    """0-1000 범위 정수 4개 + 넓이 있음 (x1==x2 나 y1==y2 인 퇴화 박스는 말풍선/가드 크롭에 못 씀)."""
    if not all(isinstance(c.get(k), int) and 0 <= c[k] <= 1000
               for k in ("x1", "y1", "x2", "y2")):
        return False
    return c["x1"] != c["x2"] and c["y1"] != c["y2"]


def added_text(original: bytes, result: bytes) -> list[dict]:
    """원본에 없던 글자 · 로고가 생성본에 생겼나 (10-03, VLM 1회 · 두 장 비교).
    반환: [{"what", "where"}] — 없으면 빈 목록. 응답이 깨지면 예외 (호출부가 "확인 못 함"으로)."""
    client = get_client()
    prompt = P.added_text_prompt()
    with observe("added_text", as_type="generation", model=vlm_model("added_text"), input=prompt) as obs:
        resp = client.models.generate_content(
            model=vlm_model("added_text"),
            contents=["Photo 1 (original):", image_part(original, "image/jpeg", "added_text"),
                      "Photo 2 (redrawn):", image_part(result, "image/jpeg", "added_text"), prompt],
            config=types.GenerateContentConfig(temperature=0, response_mime_type="application/json",
                                               thinking_config=thinking("added_text")),
        )
        data = json.loads(resp.text)
        if obs is not None:
            obs.update(output=data, usage_details=_usage(resp))
    if isinstance(data, list) and data and isinstance(data[0], dict):
        data = data[0]
    if not isinstance(data, dict) or not isinstance(data.get("added"), list):
        raise ValueError(f"added_text: 응답에 added 목록 없음: {str(data)[:200]}")
    out = []
    for a in data["added"]:
        what = _text(a.get("what")) if isinstance(a, dict) else ""
        if what:
            out.append({"what": what, "where": _text(a.get("where"))})
    return out

