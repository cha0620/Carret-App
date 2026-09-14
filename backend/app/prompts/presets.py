"""배경 스타일 프리셋.
prompt 필드는 나중에 진짜 AI 모델 연결할 때 그대로 사용된다."""

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

PRESETS = {
    "studio_white": {
        "name": "화이트 스튜디오",
        "prompt": f"professional product photography, pure white studio background, soft even lighting, subtle shadow. {SECONDHAND_LOCK}",
        "bg_color": (245, 245, 245),
    },
    "warm_wood": {
        "name": "우든 테이블",
        "prompt": f"product photo on warm wooden table, cozy natural light, shallow depth of field. {SECONDHAND_LOCK}",
        "bg_color": (160, 120, 80),
    },
    "minimal_gray": {
        "name": "미니멀 그레이",
        "prompt": f"minimalist product photography, light gray gradient background, studio lighting. {SECONDHAND_LOCK}",
        "bg_color": (210, 210, 210),
    },
}


def get_preset(key: str) -> dict:
    if key not in PRESETS:
        raise KeyError(f"알 수 없는 프리셋: {key}")
    return PRESETS[key]
