"""app.core.prompt_registry.get_prompt_text 엣지 케이스.

Langfuse 미설정(fallback) 경로가 기본이므로, 모든 테스트에서
app.core.tracing._client / _disabled 를 초기화하고 get_langfuse 를
명시적으로 monkeypatch 해서 실제 네트워크 호출이 절대 발생하지 않게 한다.
"""
import pytest

import app.core.prompt_registry as prompt_registry
import app.core.tracing as tracing


@pytest.fixture(autouse=True)
def reset_tracing_singleton(monkeypatch):
    """이전 테스트가 캐시해놓은 _client/_disabled 오염 방지."""
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", False, raising=False)
    yield


def _disable_langfuse(monkeypatch):
    """prompt_registry 모듈 namespace 로 들어온 get_langfuse 를 직접 차단."""
    monkeypatch.setattr(prompt_registry, "get_langfuse", lambda: None)


def test_substitutes_multiple_variables(monkeypatch):
    _disable_langfuse(monkeypatch)
    result = prompt_registry.get_prompt_text(
        "x", "{{a}} + {{b}} = {{c}}", a="1", b="2", c="3",
    )
    assert result == "1 + 2 = 3"


def test_leaves_unrelated_single_brace_json_untouched(monkeypatch):
    """회귀: {{rubric}} 만 치환하고 예시 JSON의 단일 중괄호는 그대로 남아야 한다."""
    _disable_langfuse(monkeypatch)
    template = (
        'RUBRIC:\n{{rubric}}\n\n'
        'Output JSON only:\n'
        '{"analysis": "...", "fidelity": 1-5, "realism": 1-5, "trust": 1-5}'
    )
    result = prompt_registry.get_prompt_text("judge_system", template, rubric="R1\nR2")
    assert "R1\nR2" in result
    assert "{{rubric}}" not in result
    # JSON schema example must survive untouched (a naive .format() would crash here)
    assert '{"analysis": "...", "fidelity": 1-5, "realism": 1-5, "trust": 1-5}' in result


def test_falls_back_cleanly_when_langfuse_get_prompt_raises(monkeypatch):
    class ExplodingClient:
        def get_prompt(self, *a, **kw):
            raise RuntimeError("langfuse network boom")

    monkeypatch.setattr(prompt_registry, "get_langfuse", lambda: ExplodingClient())
    result = prompt_registry.get_prompt_text("x", "fallback {{v}}", v="ok")
    assert result == "fallback ok"


def test_falls_back_cleanly_when_compile_raises(monkeypatch):
    class BadPrompt:
        def compile(self, **kw):
            raise ValueError("bad compile")

    class ClientWithBadCompile:
        def get_prompt(self, *a, **kw):
            return BadPrompt()

    monkeypatch.setattr(prompt_registry, "get_langfuse", lambda: ClientWithBadCompile())
    result = prompt_registry.get_prompt_text("x", "fallback {{v}}", v="ok")
    assert result == "fallback ok"


def test_uses_langfuse_compiled_text_when_client_succeeds(monkeypatch):
    captured_kwargs = {}

    class FakePrompt:
        def compile(self, **kw):
            captured_kwargs.update(kw)
            return "REMOTE COMPILED: " + ", ".join(f"{k}={v}" for k, v in kw.items())

    class FakeClient:
        def get_prompt(self, name, label=None, fallback=None, max_retries=None,
                        fetch_timeout_seconds=None):
            assert name == "judge_system"
            assert label == "production"
            return FakePrompt()

    monkeypatch.setattr(prompt_registry, "get_langfuse", lambda: FakeClient())
    result = prompt_registry.get_prompt_text("judge_system", "local fallback", rubric="R")
    assert result == "REMOTE COMPILED: rubric=R"
    assert captured_kwargs == {"rubric": "R"}


