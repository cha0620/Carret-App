"""app.services.pipeline — 순수 규칙 (그래프 없이).

- 생성 전 판단 표 (_composite_first_reason)
- verify 체크리스트에 걸 글자 고르기 (_key_texts · _verify_targets) — 글자 하나가 통과 조건이 되므로
  어떤 글자를 거느냐가 오반려·놓침을 정한다
- 게이트 재생성 문구 (mark_gate_retry)

경로(어느 노드로 가나)는 test_pipeline_scenarios.py 가 그래프 끝까지 돌려서 본다.
"""

import pytest

import app.services.pipeline as pipeline_mod

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
    ({"photo_type": "inside_view"}, "inside_view"),])
def test_composite_first_reason(state, expected):
    assert pipeline_mod._composite_first_reason(state) == expected


def test_key_texts_cap_keeps_largest_and_counts_only_kept():
    texts = [{"text": "x"}] * 5 + [_box(f"t{i:02d}", 0, 0, i + 1, 1) for i in range(20)]
    out = pipeline_mod._key_texts(texts)
    assert MAX == 8
    assert [t for t, _ in out] == [f"t{i:02d}" for i in range(19, 11, -1)]


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


# ══ 게이트 재생성 문구 ═════════════════════════════════════════════
def test_mark_gate_retry_note_lists_only_lost_marks():
    out = pipeline_mod.mark_gate_retry({"checks": [
        {"what": "BRAUN", "preserved": False}, {"what": "ok", "preserved": True}]})
    assert "lost or altered these marks on the product" in out["gate_note"]
    assert '"BRAUN"' in out["gate_note"] and '"ok"' not in out["gate_note"]
    assert out["gate_retried"] is True and out["photo_check"] is None


# ══ 10-03: 팔 물건 고르기 (apply_selection) ═════════════════════════
def _obj(what, x1, y1, x2, y2, for_sale=True):
    return {"what": what, "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}, "for_sale": for_sale}


CD1, CD2 = _obj("CD", 50, 100, 450, 900), _obj("CD", 550, 100, 950, 900)
KB = _obj("keyboard", 0, 0, 1000, 80, for_sale=False)
CASE = _obj("case", 300, 950, 700, 1000)


def _analysis(objects=(CD1, CD2, KB), **kw):
    return {"item": "CD", "item_count": 2, "item_box": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
            "objects": list(objects), "detect_failed": False, **kw}


def test_apply_selection_picks_two_unions_boxes_and_leaves_out_rest():
    out = pipeline_mod.apply_selection(_analysis(), [0, 1])
    assert out["item_count"] == 2 and out["item"] == "CD"
    assert out["item_box"] == {"x1": 50, "y1": 100, "x2": 950, "y2": 900}
    assert out["leave_out"] == ["keyboard"]
    assert out["leave_out_boxes"] == [KB["box"]] and out["sell_boxes"] == [CD1["box"], CD2["box"]]


def test_apply_selection_one_of_two_same_name_leaves_out_by_box_only():
    """CD 2장 중 1장 — 이름으로 빼라고 하면 남길 CD 까지 지운다. 박스로만 뺀다."""
    out = pipeline_mod.apply_selection(_analysis(), [0])
    assert out["item"] == "CD" and out["item_count"] == 1
    assert out["leave_out"] == ["keyboard"]
    assert out["leave_out_boxes"] == [CD2["box"], KB["box"]] and out["sell_boxes"] == [CD1["box"]]


def _t(text, x1, y1, x2, y2):
    return {"text": text, "x1": x1, "y1": y1, "x2": x2, "y2": y2}


def test_apply_selection_drops_texts_only_on_unchosen_objects():
    texts = [_t("ON_CD1", 100, 400, 300, 500),     # 고른 CD1 위
             _t("ON_KB", 500, 10, 600, 50),        # 안 고른 키보드 위
             _t("ON_CD2", 600, 400, 800, 500),     # 안 고른 CD2 위
             _t("NOWHERE", 460, 950, 540, 990),    # 어느 박스에도 없음 → 남긴다
             {"text": "NO_BOX"}]                   # 위치 모름 → 남긴다
    out = pipeline_mod.apply_selection(_analysis(item_texts=texts), [0])
    assert [t["text"] for t in out["item_texts"]] == ["ON_CD1", "NOWHERE", "NO_BOX"]


def test_apply_selection_text_in_overlap_of_chosen_and_unchosen_is_kept():
    """키보드 박스(위쪽 띠)와 CD1 박스가 겹치는 곳의 글자 — 고른 물건에도 있으니 남긴다."""
    cd = _obj("CD", 0, 0, 500, 500)
    out = pipeline_mod.apply_selection(_analysis((cd, KB), item_texts=[_t("X", 100, 20, 200, 60)]), [0])
    assert [t["text"] for t in out["item_texts"]] == ["X"]


@pytest.mark.parametrize("sell", [[True], [False, True], [-1],])
def test_apply_selection_bad_indices_only_leaves_analysis(sell):
    """잘못된 번호 · bool · 문자열 · 실수만 고르면 아무것도 안 고른 것 — 분석 그대로."""
    a = _analysis()
    assert pipeline_mod.apply_selection(a, sell) == a


@pytest.mark.parametrize("objects", [None, []])
def test_apply_selection_without_objects_ignores_sell(objects):
    """옛 분석(objects 없음)에 sell 이 와도 그대로 — 번호를 맞출 목록이 없다."""
    a = _analysis(objects=())
    a["objects"] = objects
    assert pipeline_mod.apply_selection(a, [0]) == a


@pytest.mark.parametrize("answer", [1, 3, 12])
def test_apply_selection_answer_count_overrides_analysis_and_selection(answer):
    assert pipeline_mod.apply_selection(_analysis(), None, answer)["item_count"] == answer
    out = pipeline_mod.apply_selection(_analysis(), [0, 1], answer)
    assert out["item_count"] == answer and out["leave_out"] == ["keyboard"]


