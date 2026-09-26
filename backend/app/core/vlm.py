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
#   item_text : 2026-09-26 Langfuse 에서 1회 생각 62,912 토큰($0.57, 최근 500건 비용의 25%) — 정상
#               호출은 276~1,044 토큰이라 verify 와 같은 상한으로 폭주만 막는다
#   detect, judge, check_photo, match : 기본값(생각 켬)은 유지하되 폭주만 막는 넉넉한 상한 — 최근
#               500건 중 1,000 토큰을 넘은 적이 거의 없어 판정엔 영향이 없고, 최악만 막는다
DEFAULT_THINKING: dict[str, str | int] = {
    "verify": 2048,
    "item_text": 2048,
    "detect": 4096,
    "judge": 4096,
    "check_photo": 4096,
    "match": 4096,
    "classify": "minimal",        # lite 모델에서 돈다 (3.8-flash 는 minimal 을 거부)
    "auto_feedback": "low",       # 기본 모델(3.8-flash)이 minimal 을 400 으로 거부 — low 는 둘 다 받는다
}


# 호출 이름별 모델 — 비어 있으면 전부 settings.VLM_MODEL. 호출마다 필요한 눈이 달라서(품목 분류·구도
# 확인은 큰 그림, detect/verify/item_text 는 작은 흠집·글씨) 싼 모델로 돌려도 되는 호출이 있다.
# 2026-09-26 비교(로컬 17쌍, 같은 조건, 기준 gemini-3.5-flash):
#   classify, check_photo → 3.5-flash-lite: 품목 16/17 같은 뜻, valid 15/17 (3.8-flash 도 15/17), 단가 ~1/5
#   item_text: 3.8-flash 기준 글자 71%, lite 13%(JSON 깨짐 6건) / detect: 앵커 32→24(3.8)·17(lite)
#   verify: 둘 다 게이트 판정 11/15
# 같은 날 진짜 하자 세트(19장, storage/real_defects) detect: 3.8 은 진짜 하자를 3.5 만큼 찾고, 3.5 가 잡은
#   헛하자(부엉이 찻잔 물결 테두리, 빈티지 마감 찬장)는 잡지 않았다 → 기본 모델(VLM_MODEL)을 3.8-flash 로.
#   judge 도 3.8 로 바뀌어 성적표 점수의 기준선이 이날부터 달라진다 (이전 점수와 직접 비교 금지).
# 주의: 3.8-flash 는 thinking_level "minimal" 을 400 으로 거부한다 — 모델을 바꿀 땐 DEFAULT_THINKING 도 확인.
DEFAULT_MODELS: dict[str, str] = {
    "classify": "gemini-3.5-flash-lite",
    "check_photo": "gemini-3.5-flash-lite",
}


def model(name: str) -> str:
    """호출 이름 → Gemini 모델 id."""
    m = {**DEFAULT_MODELS, **settings.vlm_models}.get(name)
    return str(m).strip() if m and str(m).strip() else settings.VLM_MODEL


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


# 이미지 해상도 — gemini-3.x 는 픽셀 크기가 아니라 이 등급으로 이미지 토큰이 정해진다 (2026-09-26
# count_tokens: 384px 로 줄여도 그대로, low 268 / medium 542 / high(기본) 1,066). 이미지를 줄여 보내는
# 건 비용에 효과가 없다. 작은 흠집·글씨를 봐야 하는 detect/verify/item_text 는 기본(high) 유지,
# 물건 종류·구도·자막만 보는 호출은 low — 둘 다 비용의 75~79% 가 입력(이미지)이었다.
MEDIA_LEVELS = {"low", "medium", "high"}
DEFAULT_MEDIA_RESOLUTION: dict[str, str] = {
    "classify": "low",
    "check_photo": "low",
}


def media_resolution(name: str) -> types.PartMediaResolution | None:
    """호출 이름 → 이미지 Part 에 붙일 해상도. None 이면 모델 기본값(high)."""
    level = str({**DEFAULT_MEDIA_RESOLUTION, **settings.vlm_media_resolution}
                .get(name, "default")).strip().lower()
    if level in MEDIA_LEVELS:
        return types.PartMediaResolution(level=f"MEDIA_RESOLUTION_{level.upper()}")
    if level != "default":
        logger.warning(f"VLM_MEDIA_RESOLUTION[{name}]={level!r} 알 수 없는 값 — 모델 기본값 사용")
    return None


def image_part(data: bytes, mime_type: str, name: str) -> types.Part:
    """이미지 Part — 호출 이름별 해상도(media_resolution)를 붙인다."""
    part = types.Part.from_bytes(data=data, mime_type=mime_type)
    res = media_resolution(name)
    if res is not None:
        part.media_resolution = res
    return part


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
