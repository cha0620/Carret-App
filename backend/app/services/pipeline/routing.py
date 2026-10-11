"""갈림길 — 생성할지 · 배경 교체할지 · 글자를 읽을지 (plan 과 라우팅 함수)."""
import logging
from app.core.config import settings

from app.services.pipeline.state import State, _result_name
from app.services.pipeline.selection import _seen, set_pieces

logger = logging.getLogger("carret.pipeline")


def _composite_first_reason(s: State) -> str | None:
    """생성 전에 이미 "생성하면 못 지킨다"가 보이는 경우 — FLUX·재생성·verify 비용을
    쓰지 않고 바로 원본 픽셀을 쓰는 배경 교체로 보낸다.
    inside_view 는 배경 교체도 하지 않는다 — plan 이 원본 그대로(keep_original)로 보낸다."""
    if s.get("detect_failed"):
        return "detect_failed"     # 생성해도 확인할 기준(원본 마크 목록)이 없다
    if s.get("photo_type") == "inside_view":
        # 엔진룸·뜯은 노트북처럼 물건 일부·내부만 찍힌 사진 — 생성은 없는 차체를 지어내고(09-27 굴삭기),
        # 오리기는 경계가 없어 엉뚱하게 잘린다. 배경을 바꿀 대상이 아니다
        return "inside_view"
    if s.get("photo_type") == "document":
        # 보증서·영수증·설명서 — 글자가 곧 물건이다 (10-03: 책·CD·음반은 상품이라 product 로 뺐다). 스펙·상태 글자(용량·주행거리)는 판매 정보로
        # 따로 받으면 되지만 제목·문서 내용은 대신할 곳이 없고, 한 글자만 바뀌어도 다른 물건이 된다 (09-27)
        return "document"
    if s.get("text_level") == "dense" and settings.text_gate:
        return "text_dense"        # 잔글씨·라벨·눈금·원본에서도 못 읽는 글자 — 생성 모델이 뭉갠다 (글자 읽기도 생략)
    if s.get("wear_level") == "heavy":
        # 녹·도장 벗겨짐처럼 하자가 넓다 — 생성은 지우거나(굴삭기 범퍼) 과장한다(승용차 녹).
        # 원본 픽셀을 쓰는 배경 교체만 상태를 그대로 보여준다
        return "wear_heavy"
    if s.get("item_cut_off"):
        # 물건이 사진 밖으로 잘렸다 — 정리하며 구도를 잡으면 안 보이던 부분을 지어낸다 (10-03 사용자 결정:
        # 전체가 안 나온 사진은 배경 제거만, 다시 찍으면 정리까지)
        return "cut_off"
    whole = set_pieces(_seen(s, 0), s.get("sell"), s.get("mains"))
    if s.get("pieces_overlap") and whole and sum(n for _, n in whole) > 2:
        # 겹침은 기준 사진 한 장으로 본다 — 참고 사진에서만 보인 구성품은 세지 않는다 (10-10 리뷰)
        # 세트 구성품이 겹쳐 서로 가린다 — 생성이 가려진 부분을 지어내고 같은 로고를 복제한다
        # (10-10 시즌그리팅: 포토카드 · 달력이 겹친 사진). 원본 픽셀로 배경만, 펼쳐 다시 찍으면 정리까지
        return "overlap"
    if (s.get("item_count") or 1) >= 2:
        # 여러 개 — 생성은 개수를 못 지킨다 (10-03 CD 2장: 개수 문장 없이 1장, 있어도 3장). 원본 픽셀로 배경만
        return "multi_item"
    # text_heavy(읽어 보니 잔글씨 多)는 read_text 가 정한다 — 여기선 아직 읽기 전이다
    return None


def plan(s: State) -> dict:
    """analyze 다음 갈림길. result_name 을 여기서 정해 두면 generate 를
    건너뛰는 경로(바로 composite)에서도 저장 이름이 있다."""
    out = {"result_name": _result_name(s)}
    if s.get("provided_result") is not None:
        return out   # dev 그래프 — 배경 교체로 가지 않는다 (inspect 에 엉뚱한 사유가 남지 않게)
    reason = _composite_first_reason(s)
    # 배경 교체를 강제하지 않고 판매자가 고른다 (10-11 사용자). 위험 이유는 risk_reason 으로 남겨 실패율 기록에 쓴다.
    # (inside_view · document · detect_failed 여도 생성을 고르면 생성 — 사용자 결정. 검사를 못 하면 화면에 "검사 못 함")
    mode = s.get("user_mode")
    # 생성과 배경 교체는 별개 기능 (10-11 사용자: "실패할 것 같아도 그냥 돌린다 — 둘은 별개"). 생성을 고르면
    # 어떤 이유로도 배경 교체 · 원본으로 돌리지 않는다 — 이유는 risk_reason 으로만 남긴다
    if mode == "generate":
        if reason:
            logger.info(f"[plan] 생성 위험({reason})이지만 판매자가 생성을 골랐다")
            out["risk_reason"] = reason
        return out
    if mode == "composite" and not reason:
        reason = "user_choice"
    if reason:
        logger.info(f"[plan] 생성하지 않음: {reason}")
        out["composite_reason"] = reason
    return out


def _needs_text(s: State) -> bool:
    """글자 읽기가 필요한가 — analyze 가 simple 이라고 봤을 때만. text_level 이 없으면(예전
    state·응답) simple 로 본다 = 예전처럼 읽는다. dense 는 배경 교체로 가서 여기 오지 않는다."""
    # 글자를 판단에서 뺐으면(text_gate=false, 10-11) 읽지도 않는다 — VLM 1회 절약
    return settings.text_lock and settings.text_gate and (s.get("text_level") or "simple") == "simple"


def _route_after_plan(s: State) -> str:
    """원본 그대로(inside_view) | 배경 교체 | 글자 읽기 후 생성 | 바로 생성 (글자 없음).
    오리기마저 실패하면 composite 가 mode 를 generate 로 되돌려 그때 생성한다 — dense 로
    갔다가 돌아온 경우는 _route_after_composite 가 read_text 를 거치게 한다."""
    if s.get("composite_reason") == "inside_view":
        return "keep_original"
    if s.get("composite_reason"):
        return "composite"
    return "read_text" if _needs_text(s) else "generate"


def _route_after_read_text(s: State) -> str:
    return "composite" if s.get("composite_reason") else "generate"


def _route_after_plan_dev(s: State) -> str:
    return "read_text" if _needs_text(s) else "use_provided"
