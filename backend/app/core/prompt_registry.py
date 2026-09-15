"""Langfuse 프롬프트 관리 계층.

키가 없거나 Langfuse 호출이 실패해도 항상 fallback(로컬 텍스트)으로 응답한다 —
tracing.py 와 같은 원칙("키 없으면 조용히 꺼짐. 테스트/CI 안전").

사용법:
    from app.core.prompt_registry import get_prompt_text
    text = get_prompt_text("judge_system", fallback=TEMPLATE, rubric=rubric_text())

variable 문법은 Langfuse 컨벤션인 이중 중괄호 {{name}} 를 쓴다 — JSON 예시 안의
단일 중괄호 {"key": ...} 와 충돌하지 않는다. label="production" 버전이 없으면
자동으로 fallback 문자열을 같은 방식으로 compile 해서 돌려준다.
"""
from app.core.tracing import get_langfuse

_LABEL = "production"


def get_prompt_text(name: str, fallback: str, **variables) -> str:
    lf = get_langfuse()
    if lf is None:
        return _compile_locally(fallback, variables)

    try:
        # 키가 잘못됐거나 호스트가 막혀 있어도 재시도/대기로 요청 지연을 만들지
        # 않도록 1회 시도 + 짧은 타임아웃만 준다 — 실패하면 즉시 fallback.
        prompt = lf.get_prompt(
            name, label=_LABEL, fallback=fallback,
            max_retries=0, fetch_timeout_seconds=3,
        )
        return prompt.compile(**variables)
    except Exception as e:
        print(f"[prompt_registry] '{name}' 조회 실패, 로컬 fallback 사용: {e}")
        return _compile_locally(fallback, variables)


def _compile_locally(template: str, variables: dict) -> str:
    for k, v in variables.items():
        template = template.replace("{{" + k + "}}", str(v))
    return template
