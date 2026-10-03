"""배경 스타일 프리셋.
prompt 필드는 나중에 진짜 AI 모델 연결할 때 그대로 사용된다.

각 프리셋 프롬프트(배경 묘사)는 Langfuse 에 "preset_<key>" 이름으로 등록해두면 그
내용을 쓰고, 없으면 아래 fallback 문구를 쓴다. SECONDHAND_LOCK 은 어느 쪽이든
get_preset() 이 코드에서 붙인다 — 콘솔 편집으로 지워질 수 없게."""
import hashlib
import re

from app.core.prompt_registry import get_prompt_text

# ⭐ 핵심 정직성 잠금 — 배경만 바꾸고 물건 자체는 절대 복원/보정하지 않도록
# 모든 프리셋 프롬프트에 공통으로 붙인다. (README "change the background,
# never the truth" 원칙의 실제 구현 지점. 이게 없으면 편집 모델이 얼룩/흠집을
# 지워버릴 수 있고, Wear Gate 는 사후에만 잡아낼 뿐 생성 자체를 막지 못한다.)
# 10-03: 틀 지시를 약하게 — "center it, fill most of the frame" 에 맞추려다 개수 · 모양을 바꿨다 (CD 2장 → 1 · 3장).
#   "가운데 근처 · 여백 넉넉히" 정도로만 (구도는 참고).
# 10-03: "— no other items and no text" 를 뺐다 — 금지하려고 단어를 쓰면 그게 그릴 단서가 된다
#   (구도 문장의 "CD case" · "shrink wrap" 이 그대로 그려졌다). 원본에 없는 건 더하지 말라는 말만 남긴다.
# 10-01 사용자 결정: 쇼핑몰 진열 사진처럼 "정리"는 한다 (자세·구김·옷걸이·손) — 상태(하자·포장)는 그대로.
# 정리와 하자 삭제는 모델에게 같은 동작이라, 하자 쪽 문장을 구체적으로(같은 자리·같은 크기) 적는다.
# 10-01 저녁: 구도를 다시 연출하게 한 잠금(listing)은 실패로 정리 — 한 장으로 각도를 바꾸면 안 보이던 면을
# 지어내고(가려진 깔창 로고, 신발 옆면), 종류별 예시를 넣으면 다른 물건을 그렸다(신발 뒤 재킷). 이제 구도는
# 판매자가 여러 각도로 찍은 사진 중 고른 한 장이 정한다 (api/routes/items.py).
# 10-01 밤: 각도 유지 · 손 가림 문장은 뺐다 — 빠진 각도와 가려진 사진은 업로드 단계(services/coverage.py)가
# 다시 찍어 달라고 안내한다. 잠금은 "정리는 하되 없는 걸 더하지 말고, 물건 자체는 그대로" 세 덩어리만.
SECONDHAND_LOCK = (
    "Tidy it into a clean listing photo: place it near the center with comfortable margins, smooth out "
    "wrinkles, and leave out hangers, hands and props that are not part of the product. "
    "Do not add anything that is not in the original photo. "
    "Keep the product itself exactly as it is: its shape, color, pattern, texture, parts, logos and "
    "printed text, any packaging or tags, and every stain, scratch, tear, hole, fading and wear mark "
    "in the same place and at the same size. Do not repair, clean or restore it."
)
# 예전 잠금 — Langfuse 에 이 문구가 들어간 옛 프롬프트가 남아 있으면 떼어낸다 ("배경만 바꿔라"가 남아 새 잠금과 부딪치지 않게)
LEGACY_LOCKS = (
    # 10-03 낮 ("no text" 를 뺀 뒤, 틀 지시가 아직 셌던 판)
    "Tidy it into a clean listing photo: center it, fill most of the frame, smooth out wrinkles, and "
    "leave out hangers, hands and props that are not part of the product. "
    "Do not add anything that is not in the original photo. "
    "Keep the product itself exactly as it is: its shape, color, pattern, texture, parts, logos and "
    "printed text, any packaging or tags, and every stain, scratch, tear, hole, fading and wear mark "
    "in the same place and at the same size. Do not repair, clean or restore it.",
    # 10-01 ~ 10-03 ("no other items and no text" 가 있던 판)
    "Tidy it into a clean listing photo: center it, fill most of the frame, smooth out wrinkles, and "
    "leave out hangers, hands and props that are not part of the product. "
    "Do not add anything that is not in the original photo — no other items and no text. "
    "Keep the product itself exactly as it is: its shape, color, pattern, texture, parts, logos and "
    "printed text, any packaging or tags, and every stain, scratch, tear, hole, fading and wear mark "
    "in the same place and at the same size. Do not repair, clean or restore it.",
    # viewpoint (10-01 저녁, 각도 유지 · 손 조건부) — 과하고 넓어서 정리
    "Keep the camera angle and viewpoint of this photo: do not rotate the product or show it from "
    "another side. Tidy it into a clean listing photo: center it, fill most of the frame, smooth out "
    "wrinkles from folding or hanging, and leave out hangers, people, other items and props that are "
    "not part of the product. Remove a hand only if nothing of the product is hidden behind it; if a "
    "hand covers part of the product, keep that part as it is rather than inventing what is underneath. "
    "Show only the product that is in the original photo: do not add any other item, garment, "
    "mannequin or accessory. Add no captions, titles, size labels or other text anywhere in the image. "
    "Keep the material's own texture and sheen. "
    "Do not repair, clean, restore, or hide its condition: keep every stain, scratch, scuff, tear, "
    "hole (including distressed holes), dent, fading, discoloration, pilling, crack, and wear mark "
    "exactly as in the original photo, in the same place and at the same size, and keep any "
    "packaging (such as shrink wrap or tags) as it is. "
    "Keep the product's shape, color, pattern, parts, and every printed logo and text exactly the same.",
    # listing (10-01 오후, 구도 다시 연출) — 실패로 정리
    "Restage the product as a neat listing photo for an online store: place it centered, upright, "
    "facing the camera and neatly arranged, filling most of the frame. You may change its pose, angle "
    "and arrangement, smooth out wrinkles from folding or hanging, and leave out hangers, hands, people, "
    "other items and props that are not part of the product. "
    "Show only the product that is in the original photo: do not add any other item, garment, "
    "mannequin or accessory. "
    "Keep the material's own texture and sheen: do not turn crinkled, glossy or knitted fabric into a "
    "different finish. Where a part was hidden in the original photo, show it plainly and do not "
    "invent logos, text, labels or details there. Add no captions, titles, size labels or other text "
    "anywhere in the image. "
    "Do not repair, clean, restore, or hide its condition: keep every stain, scratch, scuff, tear, "
    "hole (including distressed holes), dent, fading, discoloration, pilling, crack, and wear mark "
    "exactly as in the original photo, in the same place on the product and at the same size, and keep "
    "any packaging (such as shrink wrap or tags) as it is. "
    "Keep the product's shape, color, pattern, parts, and every printed logo and text exactly the same.",
    "Present the product like a clean e-commerce catalog shot: you may straighten its pose, "
    "smooth out wrinkles and creases from folding or hanging, neaten its silhouette, and leave out "
    "hangers, hands, and props that are not part of the product. "
    "Do not repair, clean, restore, or hide its condition: keep every stain, scratch, scuff, tear, "
    "hole, dent, fading, discoloration, pilling, crack, and wear mark exactly as in the original photo, "
    "in the same place and at the same size, and keep any packaging (such as shrink wrap or tags) as it is. "
    "Keep the product's shape, color, pattern, parts, and every printed logo and text exactly the same.",
    "Do not repair, clean, restore, retouch, or smooth the product itself. "
    "Keep every stain, scratch, tear, dent, fading, pilling, crack, and "
    "printed logo/text exactly as in the original photo, pixel-identical. "
    "Only the background and lighting may change.",
)

TEXT_LOCK_MAX_LINES = 30
TEXT_LOCK_MAX_CHARS = 80


def prompt_safe(text) -> str:
    """이미지·VLM 에서 온 글자를 생성 프롬프트에 넣기 전 정리 — 개행·제어문자 제거,
    따옴표 무력화, 길이 제한 (사진에 적힌 문장이 프롬프트 지시문처럼 읽히지 않게)."""
    t = "".join(ch if ch.isprintable() else " " for ch in str(text))
    t = " ".join(t.replace('"', "'").split())
    return t[:TEXT_LOCK_MAX_CHARS]


def unreadable(text) -> bool:
    """원본에서도 못 읽은 글자가 섞였나 — 읽기 프롬프트가 못 읽는 글자를 "?" 로 쓰게 한다
    (전각 "？" 포함). 이런 글자는 생성 프롬프트(TEXT_LOCK)에도, 사후 검사에도 넣지 않는다:
    "17? 5433" 을 "letter for letter" 로 그리라고 하면 "?" 를 그리거나 모델이 채워 넣고,
    검사는 "?" 와 일치할 수 없다 (09-27 번호판). 진짜 "?" 가 들어간 상표도 빠지는 건 감수."""
    return "?" in str(text) or "？" in str(text)


def text_where(t: dict) -> str:
    """0-1000 박스 중심 → "top-left" 같은 대략 위치 (같은 글자를 엉뚱한 곳에 또 그리지 않게)."""
    if not all(k in t for k in ("x1", "y1", "x2", "y2")):
        return ""
    cx, cy = (t["x1"] + t["x2"]) / 2, (t["y1"] + t["y2"]) / 2
    v = "top" if cy < 333 else "middle" if cy < 667 else "bottom"
    h = "left" if cx < 333 else "center" if cx < 667 else "right"
    return f"{v}-{h}"


def text_lock(texts: list[dict]) -> str:
    """원본 물건 위 글자 목록 → 생성 프롬프트에 덧붙일 문구.

    생성 모델은 사진을 다시 그리며 작은 글씨·한글을 뭉개는데, 정답 글자를 알려주면
    크게 나아진다 (2026-09-24 실험: book/rolex/graphic). 위치를 같이 줘서
    같은 글자를 다른 곳에 한 번 더 그리는 부작용을 줄인다."""
    lines, seen = [], set()
    # 못 읽은 줄은 자르기 전에 원문으로 거른다 — 80자 뒤의 "?" 도 잡고, 줄 수 상한을 차지하지 않게
    readable = [t for t in texts if not unreadable(t.get("text", ""))]
    for t in readable[:TEXT_LOCK_MAX_LINES]:
        text = prompt_safe(t.get("text", ""))
        if not text:
            continue
        where = text_where(t)
        key = (text, where)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f'"{text}"' + (f" ({where})" if where else ""))
    if not lines:
        return ""
    return ("\n\nText printed on the product — keep each one exactly as in the input image, "
            "letter for letter, same font, size and position on the product. Do not retype, restyle, "
            "translate, fix, move, duplicate, or add any text: " + "; ".join(lines) + ".")


# 무드 = 배경·조명만. 물건의 색·질감·상태는 SECONDHAND_LOCK 이 지킨다 — 따뜻한 조명이 흰 물건을
# 누렇게 만들면 정직하지 않다. bg_color 는 생성하지 않는 경로(배경 교체)가 쓰는 단색 — 무드와 가까운 색.
# 소품은 넣지 않는다 (구성품으로 오해). 키를 바꾸면 저장 파일 이름·DB 키가 바뀐다.
PRESETS = {
    "studio_white": {
        "name": "화이트 스튜디오", "emoji": "🤍",
        "fallback_prompt": "professional product photography, pure white studio background, soft even lighting, subtle shadow.",
        "bg_color": (245, 245, 245),
    },
    "minimal_gray": {
        "name": "소프트 그레이", "emoji": "🩶",
        "fallback_prompt": "minimalist product photography, light gray gradient background, studio lighting.",
        "bg_color": (210, 210, 210),
    },
    "warm_wood": {
        "name": "따뜻한 우드", "emoji": "🪵",
        "fallback_prompt": "product photo on warm wooden table, cozy natural light, shallow depth of field.",
        "bg_color": (160, 120, 80),
    },
    "linen": {
        "name": "린넨 · 패브릭", "emoji": "🤎",
        "fallback_prompt": "product photo on natural beige linen fabric, soft diffused daylight, calm and clean.",
        "bg_color": (225, 215, 200),
    },
    "window_light": {
        "name": "창가 자연광", "emoji": "🪟",
        "fallback_prompt": "product photo on a light surface by a window, soft natural window light with gentle shadows.",
        "bg_color": (235, 232, 225),
    },
    "dark_mood": {
        "name": "다크 무드", "emoji": "🖤",
        "fallback_prompt": "product photo on a dark matte charcoal surface, soft moody side light, clean and minimal.",
        "bg_color": (45, 45, 48),
    },
}

# 어디에 올릴 사진인가 → 먼저 보여줄 무드 (앞이 기본값). 결과를 바꾸지 않고 고르기만 돕는다
PURPOSES = {
    "secondhand": {"name": "중고거래 대표사진", "moods": ["studio_white", "minimal_gray", "warm_wood"]},
    "feed": {"name": "감성 피드", "moods": ["window_light", "linen", "warm_wood", "dark_mood"]},
    "shop": {"name": "쇼핑몰 스타일", "moods": ["studio_white", "minimal_gray"]},
}

NOTE_MAX_CHARS = 40
# 무드 한 줄은 배경 분위기만 — 물건을 바꾸라는 요청(수리·지우기·색·글자·로고·새것)은 받지 않는다
_NOTE_BLOCK = re.compile(
    r"새\s*것|새\s*제품|새상품|고쳐|수리|복원|보정|지워|지우|없애|흠집|스크래치|얼룩|색\s*(을|깔)?\s*바꿔|"
    r"글자|문구|로고|브랜드|워터마크|사람|모델|"
    r"\b(new|repair|restore|retouch|remove|erase|fix|scratch(es)?|stains?|logos?|brand|text|"
    r"watermark|person|people|model|hands?|recolou?r)\b", re.I)


def clean_note(note) -> str:
    """사용자가 적은 무드 한 줄 → 프롬프트에 넣을 문구. 비었으면 "". 물건을 바꾸라는 요청이면 ValueError."""
    t = prompt_safe(note or "")
    if not t:
        return ""
    if len(t) > NOTE_MAX_CHARS:
        raise ValueError(f"분위기는 {NOTE_MAX_CHARS}자까지 적어 주세요")
    if _NOTE_BLOCK.search(t):
        raise ValueError("배경 분위기만 적어 주세요 — 물건을 바꾸거나 지우는 요청은 받지 않아요")
    return t


def style_key(mood: str, note: str = "") -> str:
    """저장 파일 이름·DB 키에 쓰는 스타일 이름 — 무드만이면 무드 키, 한 줄이 있으면 뒤에 해시 6자리
    (같은 사진을 다른 분위기로 다시 만들어도 앞 결과를 덮지 않게)."""
    if mood not in PRESETS:
        raise KeyError(f"알 수 없는 무드: {mood}")
    return f"{mood}-{hashlib.sha1(note.encode()).hexdigest()[:6]}" if note else mood


def mood_of(key: str) -> str:
    return key.split("-", 1)[0]


def get_preset(key: str, note: str = "") -> dict:
    """무드(또는 style_key) → 생성 프롬프트·단색. note 는 clean_note 를 거친 한 줄 — 배경에만 적용한다고
    못박아 넣고, 잠금 문구가 항상 맨 끝에 온다 (한 줄이 잠금을 뒤집지 못하게)."""
    mood = mood_of(key)
    if mood not in PRESETS:
        raise KeyError(f"알 수 없는 프리셋: {key}")
    preset = PRESETS[mood]
    body = get_prompt_text(f"preset_{mood}", fallback=preset["fallback_prompt"])
    if note:
        body += (f" Background mood: {note} — apply this to the background and lighting only; "
                 "the product itself stays exactly as in the input image.")
    return {
        "name": preset["name"],
        "prompt": with_secondhand_lock(body),
        "bg_color": preset["bg_color"],
    }


def with_secondhand_lock(prompt: str) -> str:
    """잠금 문구를 항상 맨 끝에 한 번만 붙인다. 이미 들어 있으면 떼어낸 뒤 다시
    붙인다 — Langfuse 에 잠금이 포함된 옛 버전(v1)이 production 으로 남아 있어도
    두 번 붙지 않고, 잠금 뒤에 덧붙은 지시문이 잠금을 뒤집지 못하게 한다."""
    body = prompt
    # 긴 문구부터 — 한 잠금이 다른 잠금의 일부면(실험 문구) 짧은 쪽을 먼저 떼면 긴 쪽 조각이 남는다
    for lock in sorted((SECONDHAND_LOCK, *LEGACY_LOCKS), key=len, reverse=True):
        body = body.replace(lock, "")
    body = body.strip()
    return f"{body} {SECONDHAND_LOCK}" if body else SECONDHAND_LOCK


LEAVE_OUT = ("\n\nOnly the item for sale belongs in the result: leave out the other objects that are in the "
             "photo around it.")


def leave_out(names: list[str]) -> str:
    """사용자가 팔지 않는다고 고른 물건 — 결과에서 뺀다. 물건 이름은 쓰지 않는다 (10-03: "CD case" 를 빼라고 쓰니
    고른 CD 의 투명 케이스까지 벗겼고, 이름을 안 쓰니 케이스가 남았다). 정확히 지우는 건 배경 교체의 박스(drop_boxes)."""
    return LEAVE_OUT if any(prompt_safe(x) for x in names) else ""


