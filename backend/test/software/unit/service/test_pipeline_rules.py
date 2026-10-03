"""app.services.pipeline — 순수 규칙 (그래프 없이).

- 생성 전 판단 표 (_composite_first_reason)
- verify 체크리스트에 걸 글자 고르기 (_key_texts · _verify_targets) — 글자 하나가 통과 조건이 되므로
  어떤 글자를 거느냐가 오반려·놓침을 정한다
- 게이트 재생성 문구 (mark_gate_retry)

경로(어느 노드로 가나)는 test_pipeline_scenarios.py 가 그래프 끝까지 돌려서 본다.
"""
import json

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
    # 10-03: 잘림(cut_off) · 여러 개(multi_item) — wear_heavy 다음, cut_off 가 multi_item 보다 먼저
    ({"item_cut_off": True}, "cut_off"),
    ({"item_count": 2}, "multi_item"),
    ({"item_count": 12}, "multi_item"),
    ({"item_cut_off": False}, None),
    ({"item_cut_off": None}, None),
    ({"item_count": 1}, None),
    ({"item_count": 0}, None),            # 0 · None 은 1개로 본다
    ({"item_count": None}, None),
    ({"item_cut_off": True, "item_count": 3}, "cut_off"),
    ({"wear_level": "heavy", "item_cut_off": True, "item_count": 2}, "wear_heavy"),
    ({"text_level": "dense", "item_cut_off": True}, "text_dense"),
    ({"photo_type": "document", "item_count": 2}, "document"),
    ({"photo_type": "document", "item_cut_off": True}, "document"),
    ({"photo_type": "inside_view", "item_cut_off": True, "item_count": 2}, "inside_view"),
    ({"detect_failed": True, "item_cut_off": True, "item_count": 2}, "detect_failed"),
    ({"wear_level": "light", "text_level": "simple", "item_count": 2}, "multi_item"),
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


def test_apply_selection_does_not_mutate_input():
    a = _analysis(item_texts=[{"text": "A", "x1": 0, "y1": 0, "x2": 10, "y2": 10}])
    snap = json.loads(json.dumps(a))
    pipeline_mod.apply_selection(a, [2], answer_count=5)
    assert a == snap


def test_apply_selection_order_and_duplicates_of_indices_do_not_matter():
    a = pipeline_mod.apply_selection(_analysis(), [1, 0, 1, 0])
    b = pipeline_mod.apply_selection(_analysis(), [0, 1])
    assert a == b and a["item_count"] == 2


def test_apply_selection_distinct_names_joined_in_index_order():
    out = pipeline_mod.apply_selection(_analysis((CD1, KB, CASE)), [2, 0])
    assert out["item"] == "CD and case" and out["item_count"] == 2
    assert out["item_box"] == {"x1": 50, "y1": 100, "x2": 700, "y2": 1000}
    assert out["leave_out"] == ["keyboard"]


def test_apply_selection_item_name_capped_at_three_but_count_is_all():
    objs = [_obj(n, 0, 0, 10, 10) for n in ("a", "b", "c", "d", "e")]
    out = pipeline_mod.apply_selection(_analysis(objs), [0, 1, 2, 3, 4])
    assert out["item"] == "a and b and c" and out["item_count"] == 5
    assert out["leave_out"] == [] and out["leave_out_boxes"] == []


def test_apply_selection_one_of_two_same_name_leaves_out_by_box_only():
    """CD 2장 중 1장 — 이름으로 빼라고 하면 남길 CD 까지 지운다. 박스로만 뺀다."""
    out = pipeline_mod.apply_selection(_analysis(), [0])
    assert out["item"] == "CD" and out["item_count"] == 1
    assert out["leave_out"] == ["keyboard"]
    assert out["leave_out_boxes"] == [CD2["box"], KB["box"]] and out["sell_boxes"] == [CD1["box"]]


def test_apply_selection_leave_out_names_deduplicated():
    out = pipeline_mod.apply_selection(_analysis(), [2])
    assert out["item"] == "keyboard" and out["item_count"] == 1
    assert out["item_box"] == KB["box"] and out["leave_out"] == ["CD"]
    assert out["leave_out_boxes"] == [CD1["box"], CD2["box"]]


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


def test_apply_selection_text_center_on_box_edge_counts_as_inside():
    out = pipeline_mod.apply_selection(
        _analysis((CD1, KB), item_texts=[_t("EDGE", 400, 60, 600, 100)]), [0])   # 가운데 (500, 80) = 키보드 아래 모서리
    assert out["item_texts"] == []


def test_apply_selection_without_choice_keeps_texts_as_is():
    texts = [_t("ON_KB", 500, 10, 600, 50)]
    assert pipeline_mod.apply_selection(_analysis(item_texts=texts), None)["item_texts"] == texts


def test_apply_selection_keeps_item_texts_key_absent_when_analysis_has_none():
    a = _analysis()
    assert "item_texts" not in a
    out = pipeline_mod.apply_selection(a, [0])
    assert "item_texts" not in out


def test_apply_selection_picking_only_non_sale_object_follows_user():
    """분석이 for_sale=False 로 본 물건이라도 사용자가 고르면 그것을 판다."""
    out = pipeline_mod.apply_selection(_analysis(), [2])
    assert out["item"] == "keyboard" and out["sell_boxes"] == [KB["box"]]


@pytest.mark.parametrize("sell", [[True], [False, True], [-1], [3], [99], ["0"], [0.0], [None]])
def test_apply_selection_bad_indices_only_leaves_analysis(sell):
    """잘못된 번호 · bool · 문자열 · 실수만 고르면 아무것도 안 고른 것 — 분석 그대로."""
    a = _analysis()
    assert pipeline_mod.apply_selection(a, sell) == a


def test_apply_selection_bad_indices_mixed_with_good_are_ignored():
    out = pipeline_mod.apply_selection(_analysis(), [True, -1, 9, "1", 0])
    assert out["item_count"] == 1 and out["item_box"] == CD1["box"]
    assert out["sell_boxes"] == [CD1["box"]] and out["leave_out"] == ["keyboard"]


@pytest.mark.parametrize("sell", [None, []])
def test_apply_selection_no_choice_keeps_analysis(sell):
    a = _analysis()
    assert pipeline_mod.apply_selection(a, sell) == a


@pytest.mark.parametrize("objects", [None, []])
def test_apply_selection_without_objects_ignores_sell(objects):
    """옛 분석(objects 없음)에 sell 이 와도 그대로 — 번호를 맞출 목록이 없다."""
    a = _analysis(objects=())
    a["objects"] = objects
    assert pipeline_mod.apply_selection(a, [0]) == a


def test_apply_selection_detect_failed_returned_as_is_even_with_answer_count():
    a = {"detect_failed": True, "item_count": 1}
    assert pipeline_mod.apply_selection(a, [0], answer_count=3) is a


@pytest.mark.parametrize("answer", [1, 3, 12])
def test_apply_selection_answer_count_overrides_analysis_and_selection(answer):
    assert pipeline_mod.apply_selection(_analysis(), None, answer)["item_count"] == answer
    out = pipeline_mod.apply_selection(_analysis(), [0, 1], answer)
    assert out["item_count"] == answer and out["leave_out"] == ["keyboard"]


@pytest.mark.parametrize("answer", [None, 0, -2, True, False, "3", 2.0])
def test_apply_selection_bad_answer_count_ignored(answer):
    assert pipeline_mod.apply_selection(_analysis(item_count=5), None, answer)["item_count"] == 5


def test_apply_selection_answer_count_without_objects_still_applies():
    a = {"item": "x", "item_count": 1, "detect_failed": False}
    assert pipeline_mod.apply_selection(a, None, 2)["item_count"] == 2
