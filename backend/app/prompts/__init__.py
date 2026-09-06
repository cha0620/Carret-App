"""검출 프롬프트 층 - fragment 조립식."""
from pathlib import Path

FRAG = Path(__file__).parent / "fragments"
_cache = {}

def frag(name: str) -> str:
    if name not in _cache:
        _cache[name] = (FRAG / f"{name}.md").read_text().strip()
    return _cache[name]

def _fill(t: str, **kw) -> str:
    for k, v in kw.items():
        t = t.replace("{" + k + "}", str(v))
    return t

VALID_CATEGORIES = ["surface_damage", "functional_fault", "print", "other"]

CLASSIFY_PROMPT = frag("role_classify")
DETECT_BOX_PROMPT = frag("detect_box")

def detect_prompt(item: str, considered: list) -> str:
    hints = ", ".join(considered) if considered else "any visible issue"
    return "\n\n".join([
        _fill(frag("role_detect"), item=item),
        f"Item-specific hints: {hints}",
        frag("categories"),
        frag("rules_detect"),
        frag("schema_detect"),
    ])

def verify_prompt(anchors: list, item: str, considered: list) -> str:
    lines = "\n".join(f"- {a['what']} ({a['where']})" for a in anchors)
    checklist = ", ".join(considered) if considered else "(open-ended)"
    return "\n\n".join([
        _fill(frag("role_verify"), item=item,
              checklist=checklist, lines=lines),
        frag("rules_verify"),
        frag("schema_verify"),
    ])

def match_prompt(orig, result) -> str:
    return _fill(frag("match"), orig=orig, result=result)
