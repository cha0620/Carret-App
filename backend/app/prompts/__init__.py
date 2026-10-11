"""검출 프롬프트 층 - fragment 조립식 + Langfuse 프롬프트 관리.

각 프롬프트는 Langfuse에 이름(analyze_v2/verify_v2/verify_combined/item_text/added_text/check_photo/
objects/views)으로
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


# 조각 조립 템플릿 — 런타임 fallback 과 scripts/seed_langfuse_prompts.py 가 같이 쓴다
# (따로 조립하면 둘이 어긋난다).
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


def check_photo_template() -> str:
    return "\n\n".join([frag("role_check_photo"), frag("schema_check_photo")])


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


def _mark_lines(anchors: list) -> str:
    """검사 목록 한 줄씩. loose(본구성품의 작은 인쇄, 10-05)는 [presence only] — 규칙 3이 느슨하게 본다."""
    return "\n".join(f"- {a['what']} ({a['where']})" + (" [presence only]" if a.get("loose") else "")
                     for a in anchors)


def verify_prompt(anchors: list, item: str) -> str:
    """생성본 게이트 "verify_v2" — 아이덴티티 마크 · 글자만 (옛 "verify" 는 배포된 옛 서버용으로 Langfuse 에만 남는다)."""
    lines = _mark_lines(anchors)
    return get_prompt_text("verify_v2", fallback=verify_marks_template(), item=item, lines=lines)


def verify_combined_prompt(anchors: list, item: str) -> str:
    """verify + added_text 한 호출 (10-05, settings.verify_combined) — 원본 · 생성본 두 장을 같이 본다."""
    lines = _mark_lines(anchors) or "(none)"
    return get_prompt_text("verify_combined", fallback=frag("verify_combined"), item=item, lines=lines)


def item_text_prompt(item: str) -> str:
    return get_prompt_text("item_text", fallback=frag("item_text"), item=item)


def components_prompt(n: int) -> str:
    """게시글 구성품 목록 (10-09) — 기준 + 참고 사진 n 장을 같이 보고, 세트는 부품까지 쪼갠다."""
    return get_prompt_text("components", fallback=frag("components"), n_last=str(n - 1))


def layout_plan_prompt(pieces: list[tuple[str, int]]) -> str:
    """세트 배치 정하기 (10-09) — 정답 사진의 배치를 이 구성품에 맞춰 문장으로."""
    lines = "\n".join(f"- {k} x{n}" for k, n in pieces)
    return get_prompt_text("layout_plan", fallback=frag("layout_plan"), pieces=lines)


def photo_review_prompt(n: int, with_answer: bool, needs: list[str] | None = None) -> str:
    """사진마다 무엇이 보이나 · 더 필요한 사진 (10-09) — 규칙 각도 라벨 · 필수 면 경고를 대신한다."""
    return get_prompt_text(
        "photo_review", fallback=frag("photo_review"), n_last=str(n - 1),
        answer_note=("\nThe LAST image is an ANSWER photo: a well-made listing photo of a similar product that shows the "
                     "target result. It is a different product — use it only to judge what the seller's photos still lack."
                     if with_answer else ""),
        answer_goal=" and the result can look like the answer photo" if with_answer else "",
        # 정답이 필요로 하는 부위 · 면 (각도 대신, 10-09 사용자: "게임판 앞면", "코트 상반부", "뒷면")
        needs=("\nThe listing photo must show these parts (judge base, with and missing against them):\n"
               + "\n".join(f"- {x}" for x in needs) + "\n") if needs else "")


def count_pieces_prompt(kinds: list[str]) -> str:
    """세트 구성품 종류별 개수 (10-09 개수 게이트)."""
    lines = "\n".join(f"- {k}" for k in kinds)
    return get_prompt_text("count_pieces", fallback=frag("count_pieces"), lines=lines)


def prep_check_prompt(things: list[str]) -> str:
    """정답 사진의 준비물이 판매자 사진에 다 보이나 (10-05, 스타일 참고 고르기)."""
    lines = "\n".join(f"- {t}" for t in things)
    return get_prompt_text("prep_check", fallback=frag("prep_check"), lines=lines)


def added_text_prompt() -> str:
    return get_prompt_text("added_text", fallback=frag("added_text"))


def check_photo_prompt() -> str:
    return get_prompt_text("check_photo", fallback=check_photo_template())


def objects_prompt(n: int) -> str:
    """여러 장 → 물건별로 묶기 + 사진마다 각도 (10-04). Langfuse 에 옛 "views" 프롬프트가 있어도 섞이지 않게
    이름을 따로 둔다 (응답 모양이 다르다)."""
    text = get_prompt_text("objects", fallback=frag("objects"))
    return text.replace("{{n}}", str(n)).replace("{{n_last}}", str(n - 1))


def views_prompt(n: int) -> str:
    """여러 장 각도 분류 — 사진 수를 넣는다 (Langfuse "views" 가 있으면 그 내용)."""
    text = get_prompt_text("views", fallback=frag("views"))
    return text.replace("{{n}}", str(n)).replace("{{n_last}}", str(n - 1))
