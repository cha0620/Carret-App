"""트레이싱 계층 — 키 없으면 조용히 꺼짐(noop). 테스트/CI 안전."""
from app.core.config import settings

_client = None
_disabled = False


def get_langfuse():
    global _client, _disabled
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


def start_trace(name: str, **metadata):
    lf = get_langfuse()
    return lf.trace(name=name, metadata=metadata) if lf else None


def span(trace, name: str, **kw):
    return trace.span(name=name, **kw) if trace else None


def generation(trace, name: str, **kw):
    return trace.generation(name=name, **kw) if trace else None


def end(obs, **kw):
    if obs is not None:
        obs.end(**kw)


def fail(obs, err):
    if obs is not None:
        obs.end(level="ERROR", status_message=str(err))


def flush():
    lf = get_langfuse()
    if lf:
        lf.flush()