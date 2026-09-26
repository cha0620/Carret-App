"""배경 스타일 프리셋.
prompt 필드는 나중에 진짜 AI 모델 연결할 때 그대로 사용된다.

각 프리셋 프롬프트(배경 묘사)는 Langfuse 에 "preset_<key>" 이름으로 등록해두면 그
내용을 쓰고, 없으면 아래 fallback 문구를 쓴다. SECONDHAND_LOCK 은 어느 쪽이든
get_preset() 이 코드에서 붙인다 — 콘솔 편집으로 지워질 수 없게."""
from app.core.prompt_registry import get_prompt_text

# ⭐ 핵심 정직성 잠금 — 배경만 바꾸고 물건 자체는 절대 복원/보정하지 않도록
# 모든 프리셋 프롬프트에 공통으로 붙인다. (README "change the background,
# never the truth" 원칙의 실제 구현 지점. 이게 없으면 편집 모델이 얼룩/흠집을
# 지워버릴 수 있고, Wear Gate 는 사후에만 잡아낼 뿐 생성 자체를 막지 못한다.)
SECONDHAND_LOCK = (
    "Do not repair, clean, restore, retouch, or smooth the product itself. "
    "Keep every stain, scratch, tear, dent, fading, pilling, crack, and "
    "printed logo/text exactly as in the original photo, pixel-identical. "
    "Only the background and lighting may change."
)

TEXT_LOCK_MAX_LINES = 30
TEXT_LOCK_MAX_CHARS = 80


def prompt_safe(text) -> str:
    """이미지·VLM 에서 온 글자를 생성 프롬프트에 넣기 전 정리 — 개행·제어문자 제거,
    따옴표 무력화, 길이 제한 (사진에 적힌 문장이 프롬프트 지시문처럼 읽히지 않게)."""
    t = "".join(ch if ch.isprintable() else " " for ch in str(text))
    t = " ".join(t.replace('"', "'").split())
    return t[:TEXT_LOCK_MAX_CHARS]


def _where(t: dict) -> str:
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
    for t in texts[:TEXT_LOCK_MAX_LINES]:
        text = prompt_safe(t.get("text", ""))
        if not text:
            continue
        where = _where(t)
        key = (text, where)
        if key in seen:
            continue
        seen.add(key)
        lines.append(f'"{text}"' + (f" ({where})" if where else ""))
    if not lines:
        return ""
    return ("\n\nText printed on the product — keep each one exactly as in the input image, "
            "letter for letter, same font, size and position. Do not retype, restyle, "
            "translate, fix, move, duplicate, or add any text: " + "; ".join(lines) + ".")


PRESETS = {
    "studio_white": {
        "name": "화이트 스튜디오",
        "fallback_prompt": "professional product photography, pure white studio background, soft even lighting, subtle shadow.",
        "bg_color": (245, 245, 245),
    },
    "warm_wood": {
        "name": "우든 테이블",
        "fallback_prompt": "product photo on warm wooden table, cozy natural light, shallow depth of field.",
        "bg_color": (160, 120, 80),
    },
    "minimal_gray": {
        "name": "미니멀 그레이",
        "fallback_prompt": "minimalist product photography, light gray gradient background, studio lighting.",
        "bg_color": (210, 210, 210),
    },
}


def get_preset(key: str) -> dict:
    if key not in PRESETS:
        raise KeyError(f"알 수 없는 프리셋: {key}")
    preset = PRESETS[key]
    return {
        "name": preset["name"],
        "prompt": with_secondhand_lock(
            get_prompt_text(f"preset_{key}", fallback=preset["fallback_prompt"])),
        "bg_color": preset["bg_color"],
    }


def with_secondhand_lock(prompt: str) -> str:
    """잠금 문구를 항상 맨 끝에 한 번만 붙인다. 이미 들어 있으면 떼어낸 뒤 다시
    붙인다 — Langfuse 에 잠금이 포함된 옛 버전(v1)이 production 으로 남아 있어도
    두 번 붙지 않고, 잠금 뒤에 덧붙은 지시문이 잠금을 뒤집지 못하게 한다."""
    body = prompt.replace(SECONDHAND_LOCK, "").strip()
    return f"{body} {SECONDHAND_LOCK}" if body else SECONDHAND_LOCK
