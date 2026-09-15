"""검출 프롬프트 층 - fragment 조립식 + Langfuse 프롬프트 관리.

각 프롬프트는 Langfuse에 이름(classify/detect_box/detect/verify/match)으로
등록되어 있으면 그 내용(운영자가 콘솔에서 수정 가능)을 쓰고, 없거나 조회에
실패하면 아래 fragment 조합을 그대로 fallback 으로 쓴다 — 동작은 항상 동일하게
보장된다. 변수는 Langfuse 컨벤션인 이중 중괄호 {{name}} 으로 표기한다.
"""
from pathlib import Path

from app.core.prompt_registry import get_prompt_text

FRAG = Path(__file__).parent / "fragments"
_cache = {}


def frag(name: str) -> str:
    if name not in _cache:
        _cache[name] = (FRAG / f"{name}.md").read_text().strip()
    return _cache[name]


VALID_CATEGORIES = ["surface_damage", "functional_fault", "print", "other"]


def classify_prompt() -> str:
    return get_prompt_text("classify", fallback=frag("role_classify"))


def detect_box_prompt() -> str:
    return get_prompt_text("detect_box", fallback=frag("detect_box"))


def detect_prompt(item: str, considered: list) -> str:
    hints = ", ".join(considered) if considered else "any visible issue"
    template = "\n\n".join([
        frag("role_detect"),
        "Item-specific hints: {{hints}}",
        frag("categories"),
        frag("rules_detect"),
        frag("schema_detect"),
    ])
    return get_prompt_text("detect", fallback=template, item=item, hints=hints)


def verify_prompt(anchors: list, item: str, considered: list) -> str:
    lines = "\n".join(f"- {a['what']} ({a['where']})" for a in anchors)
    checklist = ", ".join(considered) if considered else "(open-ended)"
    template = "\n\n".join([
        frag("role_verify"),
        frag("rules_verify"),
        frag("schema_verify"),
    ])
    return get_prompt_text(
        "verify", fallback=template,
        item=item, checklist=checklist, lines=lines,
    )


def match_prompt(orig, result) -> str:
    return get_prompt_text("match", fallback=frag("match"), orig=orig, result=result)
