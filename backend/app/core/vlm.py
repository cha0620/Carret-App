"""VLM(Gemini) 호출 공통 설정 — 호출 이름별 "생각(thinking)" 수준.

gemini-3.x flash 는 답하기 전에 속으로 생각하는데, 이 생각 토큰은 응답에는
안 보이지만 출력 토큰 단가로 청구된다 (verify 는 출력 195 토큰에 생각이
~2,200 토큰이었다). 단순 판단은 생각을 끄고, 좌표/세밀한 비교만 조금 남긴다.

값: "minimal" | "low" | "medium" | "high" | "default"(모델 기본값, 설정 안 함).
.env 의 VLM_THINKING='{"verify": "default"}' 처럼 호출별로 덮어쓸 수 있다.
"""
from google.genai import types

from app.core.config import settings

DEFAULT_THINKING: dict[str, str] = {}


def thinking(name: str) -> types.ThinkingConfig | None:
    level = {**DEFAULT_THINKING, **settings.vlm_thinking}.get(name, "default")
    if level == "default":
        return None
    return types.ThinkingConfig(thinking_level=level)
