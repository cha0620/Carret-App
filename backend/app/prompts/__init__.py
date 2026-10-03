"""검출 프롬프트 층 - fragment 조립식 + Langfuse 프롬프트 관리.

각 프롬프트는 Langfuse에 이름(analyze/classify/detect_box/detect/verify/item_text/
check_photo/match)으로
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


# 조각 조립 템플릿 — 런타임 fallback 과 scripts/seed_langfuse_prompts.py 가 같이 쓴다
# (따로 조립하면 둘이 어긋난다).
def detect_template() -> str:
    return "\n\n".join([
        frag("role_detect"),
        "Item-specific hints: {{hints}}",
        frag("categories"),
        frag("rules_detect"),
        frag("text_level"),
        frag("schema_detect"),
    ])


def analyze_template() -> str:
    """파이프라인 첫 단계(분석): 물건·아이덴티티 마크·사진 유형·하자 수준·워터마크·글자 수준을 한 번에.
    하자는 목록(앵커)으로 뽑지 않고 수준만 본다 — 목록으로 뽑아 "지켜라"고 하면 생성 모델이
    하자를 지어내고(2026-09-27 Braun·승용차), 목록에 없는 전반적 사용감은 어차피 못 지킨다."""
    return "\n\n".join([
        frag("role_analyze"),
        frag("rules_analyze"),
        frag("text_level_analyze"),
        frag("texts_analyze"),
        frag("schema_analyze"),
    ])


def verify_template() -> str:
    return "\n\n".join([
        frag("role_verify"),
        frag("rules_verify"),
        frag("schema_verify"),
    ])


def check_photo_template() -> str:
    return "\n\n".join([frag("role_check_photo"), frag("schema_check_photo")])


def detect_prompt(item: str, considered: list) -> str:
    hints = ", ".join(considered) if considered else "any visible issue"
    # "detect_v2": text_level·item_box_2d 를 받는 응답 형식 — 옛 "detect"(defects 만)와 이름을
    # 나눠, 배포된 옛 코드가 새 프롬프트를 받거나 코드 롤백 뒤 프롬프트만 남는 일이 없게 한다.
    return get_prompt_text("detect_v2", fallback=detect_template(), item=item, hints=hints)


def verify_marks_template() -> str:
    """파이프라인 verify (09-27~): 하자 없이 아이덴티티 마크·글자만 확인. 옛 verify 템플릿은
    "defects and marks" 를 전제해 VLM 이 목록에 없는 하자 항목을 보태 preserved:false 로 답하면
    게이트가 떨어졌다. 체크리스트(considered: stain, tear…)도 넘기지 않는다."""
    return "\n\n".join([
        frag("role_verify_marks"),
        frag("rules_verify"),
        frag("schema_verify"),
    ])


def analyze_prompt() -> str:
    # v2 (10-01): 물건 위 글자(texts)까지 읽는다 — 따로 부르던 글자 읽기(item_text)를 합침.
    # 옛 "analyze" 는 배포된 옛 서버가 쓰므로 그대로 두고 새 이름으로 (detect_v2 와 같은 방식)
    return get_prompt_text("analyze_v2", fallback=analyze_template())


def verify_prompt(anchors: list, item: str, considered: list, *, marks: bool = False) -> str:
    """marks=True: 파이프라인용 "verify_v2"(마크만). False: 옛 "verify"(하자+마크, eval·dev 리플레이)."""
    lines = "\n".join(f"- {a['what']} ({a['where']})" for a in anchors)
    if marks:
        return get_prompt_text("verify_v2", fallback=verify_marks_template(),
                               item=item, lines=lines)
    checklist = ", ".join(considered) if considered else "(open-ended)"
    return get_prompt_text(
        "verify", fallback=verify_template(),
        item=item, checklist=checklist, lines=lines,
    )


def item_text_prompt(item: str) -> str:
    return get_prompt_text("item_text", fallback=frag("item_text"), item=item)


def added_text_prompt() -> str:
    return get_prompt_text("added_text", fallback=frag("added_text"))


def check_photo_prompt() -> str:
    return get_prompt_text("check_photo", fallback=check_photo_template())


def match_prompt(orig, result) -> str:
    return get_prompt_text("match", fallback=frag("match"), orig=orig, result=result)


def views_prompt(n: int) -> str:
    """여러 장 각도 분류 — 사진 수를 넣는다 (Langfuse "views" 가 있으면 그 내용)."""
    text = get_prompt_text("views", fallback=frag("views"))
    return text.replace("{{n}}", str(n)).replace("{{n_last}}", str(n - 1))
