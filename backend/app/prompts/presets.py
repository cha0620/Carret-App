"""배경 스타일 프리셋.
prompt 필드는 나중에 진짜 AI 모델 연결할 때 그대로 사용된다."""

PRESETS = {
    "studio_white": {
        "name": "화이트 스튜디오",
        "prompt": "professional product photography, pure white studio background, soft even lighting, subtle shadow",
        "bg_color": (245, 245, 245),
    },
    "warm_wood": {
        "name": "우든 테이블",
        "prompt": "product photo on warm wooden table, cozy natural light, shallow depth of field",
        "bg_color": (160, 120, 80),
    },
    "minimal_gray": {
        "name": "미니멀 그레이",
        "prompt": "minimalist product photography, light gray gradient background, studio lighting",
        "bg_color": (210, 210, 210),
    },
}


def get_preset(key: str) -> dict:
    if key not in PRESETS:
        raise KeyError(f"알 수 없는 프리셋: {key}")
    return PRESETS[key]