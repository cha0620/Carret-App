"""VLM(Gemini) 호출 공통 설정 — 호출 이름별 "생각(thinking)" 수준.

gemini-3.x flash 는 답하기 전에 속으로 생각하는데, 이 생각 토큰은 응답에는
안 보이지만 출력 토큰 단가로 청구된다 (verify 는 출력 195 토큰에 생각이
~2,200 토큰이었다). 단순 판단은 생각을 끄고, 좌표/세밀한 비교만 조금 남긴다.

값: "minimal" | "low" | "medium" | "high" | "default"(모델 기본값, 설정 안 함),
    또는 정수 = 생각 토큰 상한(thinking_budget) — 생각은 하되 폭주만 막을 때.
.env 의 VLM_THINKING='{"verify": "default"}' 처럼 호출별로 덮어쓸 수 있다.
"""
import logging
from functools import lru_cache

from google.genai import types

from app.core.config import settings

logger = logging.getLogger("carret.vlm")
LEVELS = {"minimal", "low", "medium", "high"}

# 2026-09-24 실험(저장 결과 8장, 기본 설정과 판정 비교) 기준:
#   verify  : 기본값은 가끔 생각 ~63,000 토큰까지 폭주(1회 ~$0.57). 끄면(minimal/low) 판정이
#             어긋나고, 상한 2048 이면 8장 중 7장이 기본과 같은 판정 + 폭주 차단.
#   classify, auto_feedback : 꺼도 결과 동일(8/8, 별점 7/8) → minimal
#   detect, judge, check_photo : 끄면 앵커가 빠지거나 점수가 2점씩 흔들림, 원래 싸다 → 기본값 유지
DEFAULT_THINKING: dict[str, str | int] = {
    "verify": 2048,
    "classify": "minimal",
    "auto_feedback": "minimal",
}


def thinking(name: str) -> types.ThinkingConfig | None:
    level = {**DEFAULT_THINKING, **settings.vlm_thinking}.get(name, "default")
    if isinstance(level, bool):          # JSON true/false 가 int 로 새지 않게
        level = "default"
    if isinstance(level, int) or str(level).strip().isdigit():
        return types.ThinkingConfig(thinking_budget=int(level))
    level = str(level).strip().lower()
    if level in LEVELS:
        return types.ThinkingConfig(thinking_level=level)
    if level != "default":
        # 설정 오타 하나로 매 호출이 SDK 검증 오류를 내지 않게 — 모델 기본값으로
        logger.warning(f"VLM_THINKING[{name}]={level!r} 알 수 없는 값 — 모델 기본값 사용")
    return None


@lru_cache(maxsize=1)
def get_client():
    """Gemini 클라이언트 하나를 프로세스에서 재사용한다 — 호출마다 새로 만들면
    매번 새 HTTP 연결(TLS 핸드셰이크)을 맺는다 (변환 1회에 VLM 5~6회).
    타임아웃: 응답이 멈추면 요청 전체가 무한정 매달리지 않게 — 넘기면 예외가 나고
    각 호출부의 기존 실패 경로(재시도·폴백·검증 불가)를 탄다."""
    from google import genai
    return genai.Client(
        api_key=settings.VLM_KEY,
        http_options=types.HttpOptions(timeout=int(settings.vlm_timeout_s * 1000)))


def retryable(e: Exception) -> bool:
    """VLM 호출 실패 중 다시 시도할 가치가 있는 것만 — 타임아웃·429·5xx·깨진 응답.
    요청 자체가 틀린 4xx(400 INVALID_ARGUMENT 등)나 코드 오류(TypeError)는 다시 해도
    똑같이 실패하고 지연만 늘린다."""
    from google.genai import errors
    if isinstance(e, errors.ClientError):
        return getattr(e, "code", None) == 429
    return not isinstance(e, (TypeError, AttributeError, KeyError))
