"""app.services.pipeline — 순수 규칙 (그래프 없이).

- 생성 전 판단 표 (_composite_first_reason)
- verify 체크리스트에 걸 글자 고르기 (_key_texts · _verify_targets) — 글자 하나가 통과 조건이 되므로
  어떤 글자를 거느냐가 오반려·놓침을 정한다
- 게이트 재생성 문구 (mark_gate_retry)

경로(어느 노드로 가나)는 test_pipeline_scenarios.py 가 그래프 끝까지 돌려서 본다.
"""
import pytest

import app.services.pipeline as pipeline_mod
from app.core.config import settings
from app.prompts.presets import TEXT_LOCK_MAX_CHARS

MAX = pipeline_mod.TEXT_VERIFY_MAX


def _texts(n):
    return [{"text": f"t{i:03d}"} for i in range(n)]


def _anchors(n):
    return [{"category": "other", "what": f"a{i}", "where": "x"} for i in range(n)]


def _box(text, x1, y1, x2, y2):
    return {"text": text, "x1": x1, "y1": y1, "x2": x2, "y2": y2}


# ══ 생성 전 판단 ═══════════════════════════════════════════════════
@pytest.mark.parametrize("state,expected", [
    ({}, None),
    # 사유 하나씩
    ({"detect_failed": True}, "detect_failed"),
    ({"photo_type": "inside_view"}, "inside_view"),
    ({"photo_type": "document"}, "document"),
    ({"text_level": "dense"}, "text_dense"),
    ({"wear_level": "heavy"}, "wear_heavy"),
    # 사유가 아닌 값
    ({"detect_failed": False}, None),
    ({"photo_type": "product"}, None),
    ({"photo_type": "other"}, None),
    ({"text_level": "simple"}, None),
    ({"text_level": "none"}, None),
    ({"wear_level": "light"}, None),
    # 대소문자 정규화는 detector 몫 — 여기선 정확히 일치할 때만
    ({"photo_type": "INSIDE_VIEW", "wear_level": "HEAVY"}, None),
    ({"photo_type": "Document"}, None),
    # 경로를 바꾸지 않는 필드: 글자 수(text_heavy 는 read_text 몫)·하자 개수·워터마크
    ({"item_texts": _texts(40)}, None),
    ({"anchors": _anchors(50)}, None),
    ({"watermark": "on_item"}, None),
    # document 는 글자 수준과 무관 — 표지 글자가 적어도(none·simple) 생성하지 않는다
    ({"photo_type": "document", "text_level": "none"}, "document"),
    ({"photo_type": "document", "text_level": "simple"}, "document"),
    # 우선순위: detect_failed, inside_view, document, text_dense, wear_heavy 순
    ({"detect_failed": True, "photo_type": "inside_view", "text_level": "dense",
      "wear_level": "heavy"}, "detect_failed"),
    ({"detect_failed": True, "photo_type": "document"}, "detect_failed"),
    ({"photo_type": "inside_view", "text_level": "dense", "wear_level": "heavy"}, "inside_view"),
    ({"photo_type": "document", "text_level": "dense", "wear_level": "heavy"}, "document"),
    ({"text_level": "dense", "wear_level": "heavy"}, "text_dense"),
    ({"photo_type": "product", "text_level": "dense", "wear_level": "heavy"}, "text_dense"),
])
def test_composite_first_reason(state, expected):
    assert pipeline_mod._composite_first_reason(state) == expected


def test_composite_first_reason_ignores_min_texts(monkeypatch):
    """글자 수 기준(composite_first_min_texts)은 read_text 가 쓴다 — plan 땐 아직 안 읽었다."""
    monkeypatch.setattr(settings, "composite_first_min_texts", 0)
    assert pipeline_mod._composite_first_reason({"item_texts": _texts(40)}) is None


# ══ _key_texts: verify 에 걸 글자 ═════════════════════════════════
def test_key_texts_largest_first_with_rough_position():
    texts = [_box("small", 0, 0, 10, 10), _box("big", 700, 700, 900, 900),
             {"text": "nobox"}, _box("mid", 0, 0, 100, 100)]
    assert pipeline_mod._key_texts(texts) == [
        ("big", "bottom-right"), ("mid", "top-left"), ("small", "top-left"), ("nobox", "")]


def test_key_texts_cap_keeps_largest_and_counts_only_kept():
    texts = [{"text": "x"}] * 5 + [_box(f"t{i:02d}", 0, 0, i + 1, 1) for i in range(20)]
    out = pipeline_mod._key_texts(texts)
    assert MAX == 8
    assert [t for t, _ in out] == [f"t{i:02d}" for i in range(19, 11, -1)]


@pytest.mark.parametrize("text,kept", [
    ("", False), ("   ", False), ("A", False), ("가", False), (" A ", False),   # 한 글자는 오판이 잦다
    ("AB", True), ("가나", True), (42, True),
    ("a" * TEXT_LOCK_MAX_CHARS, True),
    ("b" * (TEXT_LOCK_MAX_CHARS + 1), False),      # prompt_safe 가 자를 긴 문장
    ("  " + "c" * TEXT_LOCK_MAX_CHARS + "  ", True),   # 앞뒤 공백은 길이에 안 셈
    ("17? 5433", False),                           # 원본에서도 못 읽은 글자 — 생성본이 "?" 와 맞을 수 없다
    ("17？ 5433", False),                          # 전각 물음표
])
def test_key_texts_keeps_only_reliable_lines(text, kept):
    assert (len(pipeline_mod._key_texts([{"text": text}])) == 1) is kept


def test_key_texts_missing_or_empty_input():
    assert pipeline_mod._key_texts(None) == []
    assert pipeline_mod._key_texts([{}, {"x1": 0}]) == []


def test_key_texts_dedup_by_lowercase_and_position():
    texts = [_box("Brand", 0, 0, 100, 100), _box("BRAND", 0, 0, 90, 90),     # 같은 top-left
             _box("brand", 900, 900, 1000, 1000),                             # 다른 위치
             {"text": "Brand"}, {"text": "bRAND"}]                            # 위치 없음 끼리
    assert pipeline_mod._key_texts(texts) == [
        ("Brand", "top-left"), ("brand", "bottom-right"), ("Brand", "")]


def test_key_texts_sanitizes_quotes_and_newlines():
    assert pipeline_mod._key_texts([{"text": 'say "hi"\nnow'}]) == [("say 'hi' now", "")]


def test_key_texts_covered_only_by_whole_word_of_three_or_more():
    """짧은 글자("ON")나 단어의 일부("IKE")가 print 앵커 문장에 걸려 검증에서 빠지면 안 된다."""
    texts = [_box("ON", 0, 0, 500, 500), _box("NIKE", 0, 0, 400, 400), _box("IKE", 0, 0, 300, 300)]
    got = [t for t, _ in pipeline_mod._key_texts(texts, covered='logo: "nike" print on chest')]
    assert got == ["ON", "IKE"]
    assert pipeline_mod._key_texts([{"text": "NIKE"}], covered="") == [("NIKE", "")]


# ══ _verify_targets: 마크 + 글자 ═══════════════════════════════════
def test_verify_targets_appends_texts_after_anchors_without_mutating_state():
    anchors = [{"category": "other", "what": "얼룩", "where": "앞면"}]
    s = {"anchors": anchors, "item_texts": [{"text": "no-box"}, _box("HELLO", 700, 700, 900, 900)]}
    assert pipeline_mod._verify_targets(s) == anchors + [
        {"category": "print", "what": 'text: "HELLO"', "where": "bottom-right"},
        {"category": "print", "what": 'text: "no-box"', "where": "on the item"},
    ]
    assert len(anchors) == 1


def test_verify_targets_only_print_anchor_covers_text():
    print_anchor = {"category": "print", "what": 'Printed logo "NIKE" on chest', "where": "앞"}
    other = {"category": "stain", "what": 'stain over "NIKE" logo', "where": "앞"}
    texts = [{"text": "nike"}, {"text": "Nike"}]
    assert pipeline_mod._verify_targets({"anchors": [print_anchor], "item_texts": texts}) == [print_anchor]
    assert pipeline_mod._verify_targets({"anchors": [other], "item_texts": texts})[-1]["what"] == 'text: "nike"'


def test_verify_targets_cap_applies_to_texts_not_anchors():
    targets = pipeline_mod._verify_targets({"anchors": _anchors(10), "item_texts": _texts(20)})
    assert len(targets) == 10 + MAX


def test_verify_targets_missing_or_none_keys():
    assert pipeline_mod._verify_targets({}) == []
    assert pipeline_mod._verify_targets({"anchors": None, "item_texts": None}) == []


# ══ 게이트 재생성 문구 ═════════════════════════════════════════════
def test_mark_gate_retry_note_lists_only_lost_marks():
    out = pipeline_mod.mark_gate_retry({"checks": [
        {"what": "BRAUN", "preserved": False}, {"what": "ok", "preserved": True}]})
    assert "lost or altered these marks on the product" in out["gate_note"]
    assert '"BRAUN"' in out["gate_note"] and '"ok"' not in out["gate_note"]
    assert out["gate_retried"] is True and out["photo_check"] is None


def test_mark_gate_retry_nothing_lost_no_note():
    assert pipeline_mod.mark_gate_retry({"checks": [{"what": "a", "preserved": True}]})["gate_note"] == ""
