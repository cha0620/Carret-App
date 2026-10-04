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


def test_detect_prompt_contains_item_and_joined_hints():
    text = P.detect_prompt("hoodie", ["stain", "tear", "pilling"])
    assert "hoodie" in text
    assert "stain, tear, pilling" in text


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


def test_verify_prompt_no_placeholders_leak():
    text = P.verify_prompt([{"what": "a", "where": "b"}], "item", ["c"])
    assert "{{item}}" not in text
    assert "{{checklist}}" not in text
    assert "{{lines}}" not in text


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


def test_verify_prompt_marks_compiles_item_and_lines():
    anchors = [{"what": "BRAUN", "where": "front"}, {"what": "Series 9", "where": "side"}]
    text = P.verify_prompt(anchors, "shaver", ["stain", "tear"], marks=True)
    assert "shaver" in text and "- BRAUN (front)" in text and "- Series 9 (side)" in text
    assert "{{" not in text
    assert "stain" not in text                  # 하자 체크리스트는 넣지 않는다


def test_text_lock_skips_unreadable_lines():
    from app.prompts.presets import text_lock
    out = text_lock([{"text": "17? 5433"}, {"text": "HYUNDAI"}, {"text": "A？B"}])
    assert '"HYUNDAI"' in out and "?" not in out and "？" not in out


