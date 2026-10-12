"""갈림길 — 생성할지 · 배경 교체할지 · 글자를 읽을지 (plan 과 라우팅 함수)."""
import logging
from app.core.config import settings

from app.services.pipeline.state import State, _result_name

logger = logging.getLogger("carret.pipeline")


def plan(s: State) -> dict:
    """analyze 다음 갈림길. result_name 을 여기서 정해 두면 generate 를
    건너뛰는 경로(바로 composite)에서도 저장 이름이 있다."""
    out = {"result_name": _result_name(s)}
    if s.get("provided_result") is not None:
        return out   # dev 그래프 — 배경 교체로 가지 않는다 (inspect 에 엉뚱한 사유가 남지 않게)
    # 10-11 사용자: 생성 전에 배경 교체로 보내던 판단(잘림 · 겹침 · 여러 개 · 사용감 · 문서 · 내부 · 분석 실패 · 글자)은
    # 지웠다 — 먼저 생성해 보고 얼마나 깨지는지 보고 정한다. 사진 쪽 확인은 "정답 구도를 만들 사진이 다 있나"(사진 검토 fit)만.
    # 배경 교체는 판매자가 [배경만 바꾸기]를 골랐을 때만
    if s.get("user_mode") == "composite":
        logger.info("[plan] 판매자가 배경 교체를 골랐다")
        out["composite_reason"] = "user_choice"
    return out


def _needs_text(s: State) -> bool:
    """글자 읽기가 필요한가 — analyze 가 simple 이라고 봤을 때만. text_level 이 없으면(예전
    state·응답) simple 로 본다 = 예전처럼 읽는다. """
    # 글자를 판단에서 뺐으면(text_gate=false, 10-11) 읽지도 않는다 — VLM 1회 절약
    return settings.text_lock and settings.text_gate and (s.get("text_level") or "simple") == "simple"


def _route_after_plan(s: State) -> str:
    """배경 교체(판매자가 골랐을 때) | 글자 읽기 후 생성 | 바로 생성."""
    if s.get("composite_reason"):
        return "composite"
    return "read_text" if _needs_text(s) else "generate"


def _route_after_read_text(s: State) -> str:
    return "composite" if s.get("composite_reason") else "generate"


def _route_after_plan_dev(s: State) -> str:
    return "read_text" if _needs_text(s) else "use_provided"
