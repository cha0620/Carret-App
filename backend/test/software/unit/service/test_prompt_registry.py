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


def test_returns_fallback_verbatim_when_no_variables_and_langfuse_disabled(monkeypatch):
    _disable_langfuse(monkeypatch)
    fallback = "hello world, no vars here"
    assert prompt_registry.get_prompt_text("anything", fallback) == fallback


def test_substitutes_single_variable(monkeypatch):
    _disable_langfuse(monkeypatch)
    result = prompt_registry.get_prompt_text("x", "hi {{item}}!", item="carret")
    assert result == "hi carret!"


def test_variable_named_name_is_shadowed_by_positional_name_param(monkeypatch):
    """실동작 확인용 회귀 테스트(버그 아님, API 제약 문서화):
    get_prompt_text(name, fallback, **variables) 시그니처상 'name' 은 프롬프트
    식별자로 예약돼 있어서, 템플릿 변수로 키워드 인자 name= 을 넘기면
    TypeError 가 난다. 호출부는 반드시 'name' 이 아닌 다른 변수명을 써야 한다."""
    with pytest.raises(TypeError):
        prompt_registry.get_prompt_text("x", "hi {{name}}!", name="carret")


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


def test_no_variables_passed_leaves_double_brace_placeholder_as_is(monkeypatch):
    """variables 를 아예 안 주면 {{var}} 자리표시자가 그대로 남아있는 것도
    정상 동작(치환 대상이 없으므로) — 크래시하지 않는 것이 중요."""
    _disable_langfuse(monkeypatch)
    result = prompt_registry.get_prompt_text("x", "keep {{untouched}}")
    assert result == "keep {{untouched}}"


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


def test_get_langfuse_none_path_does_not_call_client_at_all(monkeypatch):
    """lf is None 이면 .get_prompt 시도조차 없어야 한다(순수 로컬 compile)."""
    monkeypatch.setattr(prompt_registry, "get_langfuse", lambda: None)
    # sanity: local compile still works with empty variables dict
    assert prompt_registry.get_prompt_text("x", "plain text") == "plain text"
