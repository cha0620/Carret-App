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


def test_classify_prompt_is_callable_not_constant():
    """리팩터 회귀: 예전 CLASSIFY_PROMPT 상수 접근 방식으로 되돌아가지 않았는지."""
    assert callable(P.classify_prompt)
    assert not hasattr(P, "CLASSIFY_PROMPT")
    assert not hasattr(P, "DETECT_BOX_PROMPT")


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
