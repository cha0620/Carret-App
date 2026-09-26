"""트레이싱 계층 (Langfuse v4 OTEL API) — 키 없으면 조용히 꺼짐(noop). 테스트/CI 안전.

v2 API(`lf.trace()`/`trace.span()`/`trace.generation()`)는 설치된 v4 SDK에
없다(AttributeError). v4는 OTEL 기반 컨텍스트 매니저(`start_as_current_observation`)
로 span/generation을 현재 컨텍스트에 push하고, 그 안에서 호출된 observation은
자동으로 부모-자식 관계로 중첩된다 — 트레이스 객체를 직접 들고 다닐 필요가 없다.

사용법:
    from app.core.tracing import observe

    with observe("detect", as_type="generation", model=MODEL, input=prompt) as obs:
        resp = call_vlm(...)
        if obs is not None:
            obs.update(output=resp)
"""
import contextlib
import threading

from app.core.config import settings

_client = None
_disabled = False
_lock = threading.Lock()


def get_langfuse():
    """싱글톤 (double-checked locking) — FastAPI 스레드풀에서 동시 요청이
    첫 호출을 같이 타도 Langfuse 클라이언트가 두 번 만들어지지 않게."""
    global _client, _disabled
    if _disabled:
        return None
    if _client is None:
        with _lock:
            if _disabled:
                return None
            if _client is None:
                if not (settings.langfuse_public_key and settings.langfuse_secret_key):
                    _disabled = True
                    return None
                from langfuse import Langfuse
                _client = Langfuse(
                    public_key=settings.langfuse_public_key,
                    secret_key=settings.langfuse_secret_key,
                    host=settings.langfuse_host,
                )
    return _client


def current_trace_id() -> str | None:
    """지금 observe() 컨텍스트의 트레이스 id — 요청이 끝난 뒤 백그라운드 작업이
    같은 트레이스에 이어 붙을 때 넘겨준다 (observe(trace_id=...)). 없으면 None."""
    lf = get_langfuse()
    if lf is None:
        return None
    try:
        return lf.get_current_trace_id()
    except Exception:   # 트레이스 id 는 부가 정보 — 없다고 변환을 실패시키지 않는다
        return None


@contextlib.contextmanager
def observe(name: str, as_type: str = "span", *, trace_id: str | None = None,
            parent_span_id: str | None = None, **kw):
    """Langfuse 있으면 실제 observation(span/generation/...)을 현재 컨텍스트에
    push하고 그 객체를 넘겨준다(`.update(output=..., ...)`로 결과를 채우면 됨).
    Langfuse 없으면(키 미설정/비활성) 아무 일도 안 하는 noop 컨텍스트 — `None`을
    넘겨준다. 예외가 나면 SDK가 알아서 ERROR 레벨로 마킹하고 다시 던진다.
    """
    lf = get_langfuse()
    if lf is None:
        yield None
        return
    if trace_id:
        # 다른 스레드/요청 뒤에 이어 붙기 — 컨텍스트가 없으니 트레이스를 직접 지정
        kw["trace_context"] = {"trace_id": trace_id,
                               **({"parent_span_id": parent_span_id} if parent_span_id else {})}
    with lf.start_as_current_observation(name=name, as_type=as_type, **kw) as obs:
        yield obs


def score(name: str, value, **kw):
    """현재 활성 트레이스(=`observe()` 컨텍스트 안)에 점수 하나를 붙인다.
    Langfuse 없으면(키 미설정/비활성) noop — observe()와 같은 원칙."""
    lf = get_langfuse()
    if lf is not None:
        lf.score_current_trace(name=name, value=value, **kw)


def gemini_usage(resp) -> dict | None:
    """google-genai 응답의 `usage_metadata` → Langfuse `usage_details` 포맷.
    detector.py/judge.py 양쪽에서 쓰는 공용 변환기 (구조적 타이핑이라
    google-genai import 없이도 동작함)."""
    u = getattr(resp, "usage_metadata", None)
    if u is None:
        return None
    # 생각(thinking) 토큰은 응답에 안 보이지만 출력 단가로 청구된다 — output 에
    # 합쳐야 Langfuse 비용이 실제 청구액과 맞는다 (따로 보려고 thoughts 도 남김).
    thoughts = getattr(u, "thoughts_token_count", None) or 0
    return {
        "input": u.prompt_token_count or 0,
        "output": (u.candidates_token_count or 0) + thoughts,
        "thoughts": thoughts,
        "total": u.total_token_count or 0,
    }


def flush():
    lf = get_langfuse()
    if lf:
        lf.flush()