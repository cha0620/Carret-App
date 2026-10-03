"""app.prompts 의 프롬프트 조립 함수들 - Langfuse 비활성 상태에서 fallback 로직 검증.

get_prompt_text 자체의 세부 동작은 test_prompt_registry.py 에서 다루므로,
여기서는 app.core.tracing.get_langfuse() 가 None 을 반환하도록만 강제하고
(=Langfuse 완전 비활성, 실제 네트워크 호출 없음) app.prompts 함수들이
과거(리팩터 이전)와 동일한 텍스트를 만들어내는지를 검증한다.
"""
import pytest

import app.core.tracing as tracing
import app.prompts as P


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


def test_classify_prompt_matches_fragment_file_verbatim():
    assert P.classify_prompt() == P.frag("role_classify")


def test_detect_box_prompt_matches_fragment_file_verbatim():
    assert P.detect_box_prompt() == P.frag("detect_box")


def test_detect_prompt_contains_item_and_joined_hints():
    text = P.detect_prompt("hoodie", ["stain", "tear", "pilling"])
    assert "hoodie" in text
    assert "stain, tear, pilling" in text


def test_detect_prompt_empty_considered_uses_default_hint():
    text = P.detect_prompt("sneaker", [])
    assert "any visible issue" in text


def test_detect_prompt_still_contains_fragment_content():
    text = P.detect_prompt("hoodie", ["stain"])
    # role_detect.md is just "...used {{item}}." so after compile the {{item}}
    # placeholder itself is gone (replaced with "hoodie") - check the compiled
    # form rather than the raw (unfilled) fragment text.
    assert P.frag("role_detect").replace("{{item}}", "hoodie") in text
    assert P.frag("categories") in text
    assert P.frag("rules_detect") in text
    assert P.frag("text_level") in text
    assert P.frag("schema_detect") in text
    # placeholder must be gone after compile
    assert "{{hints}}" not in text
    assert "{{item}}" not in text


def test_detect_template_orders_text_level_between_rules_and_schema():
    t = P.detect_template()
    i_rules, i_level, i_schema = (t.index(P.frag(n)) for n in
                                  ("rules_detect", "text_level", "schema_detect"))
    assert i_rules < i_level < i_schema
    assert t.count(P.frag("text_level")) == 1


def test_detect_template_asks_for_text_level_and_item_box():
    t = P.detect_template()
    for word in ('"none"', '"simple"', '"dense"', "text_level", "item_box_2d"):
        assert word in t
    assert "When unsure" in P.frag("text_level")
    assert "item_box_2d" in P.frag("schema_detect")


def test_detect_prompt_requests_detect_v2_with_template_fallback(monkeypatch):
    seen = []

    def fake(name, fallback, **kw):
        seen.append((name, fallback, kw))
        return "X"
    monkeypatch.setattr(P, "get_prompt_text", fake)
    assert P.detect_prompt("mug", ["chip"]) == "X"
    assert seen == [("detect_v2", P.detect_template(), {"item": "mug", "hints": "chip"})]


def test_verify_prompt_contains_item_checklist_and_anchor_lines():
    anchors = [
        {"what": "얼룩", "where": "앞면"},
        {"what": "찢어짐", "where": "소매"},
    ]
    text = P.verify_prompt(anchors, "hoodie", ["stain", "tear"])
    assert "hoodie" in text
    assert "stain, tear" in text
    assert "- 얼룩 (앞면)" in text
    assert "- 찢어짐 (소매)" in text


def test_verify_prompt_empty_considered_uses_open_ended():
    text = P.verify_prompt([], "hoodie", [])
    assert "(open-ended)" in text


def test_verify_prompt_no_placeholders_leak():
    text = P.verify_prompt([{"what": "a", "where": "b"}], "item", ["c"])
    assert "{{item}}" not in text
    assert "{{checklist}}" not in text
    assert "{{lines}}" not in text


def test_match_prompt_contains_stringified_orig_and_result():
    orig = [{"what": "stain", "where": "front"}]
    result = [{"what": "stain", "where": "front"}]
    text = P.match_prompt(orig, result)
    assert str(orig) in text
    assert str(result) in text


def test_match_prompt_matches_fragment_template_shape():
    text = P.match_prompt([], [])
    assert "matches" in text
    assert "{{orig}}" not in text
    assert "{{result}}" not in text


# ── analyze (파이프라인 첫 단계) ──
def test_analyze_template_fragment_order():
    t = P.analyze_template()
    parts = [P.frag(n) for n in ("role_analyze", "rules_analyze", "text_level_analyze",
                                 "texts_analyze", "schema_analyze")]
    idx = [t.index(p) for p in parts]
    assert idx == sorted(idx)
    assert t == "\n\n".join(parts)


def test_analyze_template_asks_for_every_field_parsed_by_detector():
    t = P.analyze_template()
    for word in ("item", "considered", "item_box_2d", "photo_type", "wear_level", "watermark",
                 "text_level", "marks", '"texts"', '"document"', '"inside_view"', '"product"',
                 '"heavy"', '"light"', '"on_item"', '"background"', '"dense"', '"simple"'):
        assert word in t, word


def test_analyze_template_photo_type_values_match_detector():
    """스키마의 photo_type 값 = detector.PHOTO_TYPES (한쪽만 바뀌면 전부 product 로 떨어진다)."""
    from app.services.ai import detector
    t = P.analyze_template()
    for v in detector.PHOTO_TYPES:
        assert f'"{v}"' in t, v


def test_analyze_template_has_no_unfilled_placeholders():
    """analyze_prompt 는 변수를 넘기지 않는다 — {{...}} 가 남으면 모델에 그대로 간다."""
    assert "{{" not in P.analyze_template()


def test_analyze_prompt_disabled_langfuse_is_template():
    assert P.analyze_prompt() == P.analyze_template()


def test_analyze_prompt_requests_analyze_name_without_variables(monkeypatch):
    seen = []

    def fake(name, fallback, **kw):
        seen.append((name, fallback, kw))
        return "X"
    monkeypatch.setattr(P, "get_prompt_text", fake)
    assert P.analyze_prompt() == "X"
    assert seen == [("analyze_v2", P.analyze_template(), {})]   # 10-01: 글자(texts)까지 — 새 이름


# ── verify_v2 (마크 전용, 파이프라인) ──
def test_verify_marks_template_fragment_order():
    parts = [P.frag(n) for n in ("role_verify_marks", "rules_verify", "schema_verify")]
    assert P.verify_marks_template() == "\n\n".join(parts)


def test_verify_marks_template_placeholders_only_item_and_lines():
    import re
    t = P.verify_marks_template()
    assert set(re.findall(r"\{\{(\w+)\}\}", t)) == {"item", "lines"}
    assert "{{checklist}}" not in t


def test_verify_prompt_marks_compiles_item_and_lines():
    anchors = [{"what": "BRAUN", "where": "front"}, {"what": "Series 9", "where": "side"}]
    text = P.verify_prompt(anchors, "shaver", ["stain", "tear"], marks=True)
    assert "shaver" in text and "- BRAUN (front)" in text and "- Series 9 (side)" in text
    assert "{{" not in text
    assert "stain" not in text                  # 하자 체크리스트는 넣지 않는다


def test_verify_prompt_marks_requests_verify_v2(monkeypatch):
    seen = []
    monkeypatch.setattr(P, "get_prompt_text",
                        lambda name, fallback, **kw: seen.append((name, fallback, kw)) or "X")
    assert P.verify_prompt([{"what": "a", "where": "b"}], "cup", ["dent"], marks=True) == "X"
    assert seen == [("verify_v2", P.verify_marks_template(), {"item": "cup", "lines": "- a (b)"})]


def test_verify_prompt_default_is_old_verify(monkeypatch):
    seen = []
    monkeypatch.setattr(P, "get_prompt_text",
                        lambda name, fallback, **kw: seen.append((name, kw)) or "X")
    P.verify_prompt([{"what": "a", "where": "b"}], "cup", ["dent"])
    assert seen == [("verify", {"item": "cup", "checklist": "dent", "lines": "- a (b)"})]


def test_verify_prompt_marks_empty_anchors():
    text = P.verify_prompt([], "cup", [], marks=True)
    assert "{{" not in text


# ── presets.unreadable / text_lock ──
@pytest.mark.parametrize("text,expected", [
    ("17? 5433", True), ("17？ 5433", True), ("?", True), ("？", True),
    ("17가 5433", False), ("", False), ("SALE!", False), (None, False), (123, False),
])
def test_unreadable(text, expected):
    from app.prompts.presets import unreadable
    assert unreadable(text) is expected


def test_text_lock_skips_unreadable_lines():
    from app.prompts.presets import text_lock
    out = text_lock([{"text": "17? 5433"}, {"text": "HYUNDAI"}, {"text": "A？B"}])
    assert '"HYUNDAI"' in out and "?" not in out and "？" not in out


def test_text_lock_all_unreadable_is_empty():
    from app.prompts.presets import text_lock
    assert text_lock([{"text": "17? 5433"}, {"text": "？"}]) == ""
