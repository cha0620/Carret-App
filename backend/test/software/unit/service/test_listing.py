"""services/listing — 여러 장 업로드를 물건 단위로 (10-04).

normalize(VLM 응답 정리) · apply(분류 결과 합치기, 사용자가 고친 묶음 지키기) · edit(사용자 수정 검사)
· ensure_objects(옛 묶음) · summary / main_object(물건별 빠진 면 · 대표 물건). 순수 함수라 VLM · storage 없이 본다.
"""
import copy

import pytest

from app.services import listing

DEFAULT_FLAGS = {"state": None, "occluded": False, "blurry": False, "item_visible": True}   # state: subtype 물건 사진만 (10-04)


def _obj(id_, kind="product", **kw):
    """VLM 이 낼 법한 물건 하나."""
    base = {"id": id_, "kind": kind, "name": "thing", "label": "물건", "desc": "",
            "category": "other" if kind == "product" else None, "for_sale": kind == "product"}
    if kind == "proof":
        base.update(proof_type="document", proof_for=None)
    return {**base, **kw}


def _ph(index, obj, view=None, **kw):
    return {"index": index, "object": obj, "view": view, **kw}


# ═════════════════════════ normalize ═════════════════════════
def test_normalize_basic_two_objects_ids_renumbered():
    out = listing.normalize({
        "objects": [_obj("A", name="sneakers", label="흰 운동화", category="shoes"),
                    _obj("B", name="bag", category="bag")],
        "photos": [_ph(0, "A", "front_34"), _ph(1, "B", "front"), _ph(2, "A", "bottom")],
    }, 3)
    assert out["objects"][0] == {"id": "o1", "kind": "product", "name": "sneakers", "label": "흰 운동화",
                                 "desc": "", "proof_type": None, "proof_for": None, "category": "shoes",
                                 "subtype": None, "for_sale": True, "count": 1,
                                 "role": "main", "part_of": None}
    assert [o["id"] for o in out["objects"]] == ["o1", "o2"]
    assert [p["object"] for p in out["photos"]] == ["o1", "o2", "o1"]
    assert [p["view"] for p in out["photos"]] == ["front_34", "front", "bottom"]
    assert all({k: p[k] for k in DEFAULT_FLAGS} == DEFAULT_FLAGS for p in out["photos"])


def test_normalize_proof_for_must_be_product():
    out = listing.normalize({"objects": [
        _obj("A", kind="proof", proof_for="B"),            # B 도 근거 사진 → None
        _obj("B", kind="proof", proof_for="B"),            # 자기 자신 → None
        _obj("C", kind="proof", proof_for="X"),            # 없는 물건 → None
        _obj("D", kind="proof", proof_for="P"),            # 상품 → 연결
        _obj("P"),
    ], "photos": [_ph(i, k) for i, k in enumerate("ABCDP")]}, 5)
    assert [o["proof_for"] for o in out["objects"]] == [None, None, None, "o5", None]


def test_normalize_true_index_does_not_hit_photo_one():
    out = listing.normalize({"objects": [_obj("A")],
                             "photos": [{"index": True, "object": "A", "view": "back"},
                                        {"index": 0, "object": "A", "view": "front"}]}, 2)
    assert out["photos"][1]["object"] is None and out["photos"][0]["view"] == "front"


def test_normalize_objects_but_no_photo_attached_falls_back_to_one():
    """물건은 냈지만 사진을 하나도 안 붙였으면 — 묶음 없는 응답처럼 모든 사진을 물건 하나로."""
    out = listing.normalize({"objects": [_obj("A", name="cup"), _obj("B")], "item": "mug", "category": "other",
                             "photos": [_ph(0, "Q", "front"), _ph(1, None, "back")]}, 2)
    assert [o["name"] for o in out["objects"]] == ["mug"]
    assert [(p["object"], p["view"]) for p in out["photos"]] == [("o1", "front"), ("o1", "back")]


def test_normalize_slot_changes_kind_when_all_photos_in_one_box():
    """AI 가 상품이라 했어도 사진이 모두 근거 칸이면 근거 — 파는 상품이 하나면 proof_for 는 그 상품 (10-05)."""
    out = listing.normalize({"objects": [_obj("A"), _obj("B")],
                             "photos": [_ph(0, "A", "front"), _ph(1, "B", "front")]}, 2, ["product", "proof"])
    assert [o["kind"] for o in out["objects"]] == ["product", "proof"]
    assert out["objects"][1]["proof_for"] == "o1"
    assert out["photos"][1]["view"] is None
    b = out["objects"][1]                                  # 상품 이름이 근거에 붙지 않는다
    assert (b["label"], b["name"], b["desc"]) == ("근거 사진", "photo", "")


def test_normalize_mixed_slots_split_into_new_object():
    out = listing.normalize({"objects": [_obj("A")],
                             "photos": [_ph(0, "A", "front"), _ph(1, "A", "back")]}, 2, ["product", "proof"])
    assert [(o["id"], o["kind"]) for o in out["objects"]] == [("o1", "product"), ("o2", "proof")]
    assert [p["object"] for p in out["photos"]] == ["o1", "o2"]


def test_normalize_mixed_slots_at_max_objects_stays_on_original(monkeypatch):
    """물건 수 상한이면 떼어 내지 않고 원래 물건에 남긴다 — 사진 object 가 None 이 되지 않는다."""
    monkeypatch.setattr(listing, "MAX_OBJECTS", 1)
    out = listing.normalize({"objects": [_obj("A")],
                             "photos": [_ph(0, "A", "front"), _ph(1, "A", "back")]}, 2, ["product", "proof"])
    assert [o["id"] for o in out["objects"]] == ["o1"]
    assert [p["object"] for p in out["photos"]] == ["o1", "o1"]


def test_normalize_slot_none_keeps_ai_kind():
    out = listing.normalize({"objects": [_obj("A"), _obj("B", kind="proof")],
                             "photos": [_ph(0, "A", "front"), _ph(1, "B")]}, 2, [None, None])
    assert [o["kind"] for o in out["objects"]] == ["product", "proof"]
    assert out["objects"][1]["proof_for"] is None


@pytest.mark.parametrize("field", ["objects", "photos"])
def test_normalize_non_iterable_field_raises(field):
    """objects · photos 가 숫자면 TypeError — 호출부(_classify)가 모든 예외를 "묶지 못함"으로 처리한다."""
    with pytest.raises(TypeError):
        listing.normalize({field: 5}, 1)


# ═════════════════════════ apply ═════════════════════════
def _item(objects, photo_objs, edited=False, views=None):
    photos = [{"file_id": f"f{i}", "source": "photo", "object": o,
               "view": (views or [None] * len(photo_objs))[i], **DEFAULT_FLAGS}
              for i, o in enumerate(photo_objs)]
    return {"item_id": "x", "photos": photos, "objects": objects, "user_edited": edited}


def _mine(id_, kind="product", **kw):
    """저장된 물건 하나 (정리된 모양)."""
    return listing._clean_object(_obj(id_, kind=kind, **kw), id_)


def _out(objects, photos):
    """normalize 를 거친 AI 결과."""
    return {"objects": objects, "photos": photos}


def _aph(obj, view=None, **kw):
    return {"object": obj, "view": view, **DEFAULT_FLAGS, **kw}


def test_apply_not_edited_replaces_everything():
    item = _item([_mine("o1", label="옛 물건")], ["o1", "o1"])
    out = listing.normalize({"objects": [_obj("A", label="새 컵"), _obj("B", label="새 접시")],
                             "photos": [_ph(0, "A", "front", blurry=True), _ph(1, "B", "back")]}, 2)
    listing.apply(item, out, {"f1"})
    assert [o["label"] for o in item["objects"]] == ["새 컵", "새 접시"]
    assert [(p["file_id"], p["object"], p["view"], p["blurry"]) for p in item["photos"]] == [
        ("f0", "o1", "front", True), ("f1", "o2", "back", False)]
    assert item["photos"][0]["source"] == "photo"          # 기존 필드는 남는다
    assert "needs_review" not in item


def test_apply_edited_new_photo_follows_majority_vote():
    """AI 가 새 사진을 기존 사진 2장(내 o2)·1장(내 o1)과 같이 묶었으면 → 내 o2."""
    item = _item([_mine("o1"), _mine("o2")], ["o2", "o2", "o1", None], edited=True)
    item["photos"][3]["file_id"] = "new"
    out = _out([_mine("o1")], [_aph("o1"), _aph("o1"), _aph("o1"), _aph("o1", "back")])
    listing.apply(item, out, {"new"})
    assert item["photos"][3]["object"] == "o2" and item["photos"][3]["view"] == "back"
    assert [o["id"] for o in item["objects"]] == ["o1", "o2"]
    assert item["needs_review"] is True


def test_apply_edited_unclassified_photo_treated_as_new():
    """지난 묶기가 실패해 "모름"이 된 사진은 다음 분류 때 새 사진처럼 붙는다."""
    item = _item([_mine("o1")], ["o1", None], edited=True)
    item["photos"][1]["unclassified"] = True
    listing.apply(item, _out([_mine("o1")], [_aph("o1"), _aph("o1", "back")]), set())
    assert item["photos"][1]["object"] == "o1" and item["photos"][1]["view"] == "back"
    assert "unclassified" not in item["photos"][1] and item["needs_review"] is True


def test_apply_edited_new_ai_proof_links_to_my_product():
    """새 근거 사진(AI o1)이 AI o2 를 보증 — AI o2 는 내 o5 로 묶였으니 내 o5 를 가리킨다 (id 가 안 겹치는 경우)."""
    item = _item([_mine("o5", category="shoes")], ["o5", None], edited=True)
    item["photos"][1]["file_id"] = "new"
    ai = [{**_mine("o1", kind="proof"), "proof_for": "o2"}, _mine("o2", category="shoes")]
    listing.apply(item, _out(ai, [_aph("o2"), _aph("o1")]), {"new"})
    added = item["objects"][1]
    assert added["id"] == "o1" and added["kind"] == "proof" and added["proof_for"] == "o5"
    assert item["photos"][1]["object"] == "o1" and item["photos"][1]["view"] is None


def test_apply_edited_slot_clash_makes_new_object_of_slot_kind():
    """AI 가 새 근거 칸 사진 2장을 내 상품(o1)과 묶어도 칸이 다르니 근거 물건 하나를 새로 만들어 모은다."""
    item = _item([_mine("o1")], ["o1", None, None], edited=True)
    for i in (1, 2):
        item["photos"][i].update(file_id=f"new{i}", slot="proof")
    listing.apply(item, _out([_mine("o1")], [_aph("o1"), _aph("o1", "back"), _aph("o1", "left")]), {"new1", "new2"})
    assert [(o["id"], o["kind"]) for o in item["objects"]] == [("o1", "product"), ("o2", "proof")]
    assert [p["object"] for p in item["photos"]] == ["o1", "o2", "o2"]
    assert item["photos"][1]["view"] is None


# ═════════════════════════ edit ═════════════════════════
def _edited_item():
    """AI 가 묶은 상태: o1 운동화(사진 f0,f1) · o2 보증서(f2, o1 의 근거)."""
    objects = [_mine("o1", name="sneakers", label="운동화", category="shoes"),
               _mine("o2", kind="proof", name="warranty card", label="보증서")]
    objects[1]["proof_for"] = "o1"
    return _item(objects, ["o1", "o1", "o2"], views=["front_34", "side", None])


def _body(objects=None, photos=None):
    objects = objects if objects is not None else [
        {"id": "o1", "kind": "product", "label": "흰 운동화", "desc": "깨끗함", "category": "shoes",
         "for_sale": True, "count": 1, "proof_type": None, "proof_for": None},
        {"id": "o2", "kind": "proof", "label": "보증서", "desc": "", "category": None, "for_sale": False,
         "count": 1, "proof_type": "document", "proof_for": "o1"},
    ]
    photos = photos if photos is not None else [
        {"file_id": "f0", "object": "o1", "view": "front_34"},
        {"file_id": "f1", "object": "o1", "view": "side"},
        {"file_id": "f2", "object": "o2", "view": None},
    ]
    return {"objects": objects, "photos": photos}


def test_edit_ok_sets_flags_and_keeps_english_name():
    item = _edited_item()
    item["needs_review"] = True
    assert listing.edit(item, _body()) == []
    assert item["user_edited"] is True and item["needs_review"] is False
    o1, o2 = item["objects"]
    assert o1["name"] == "sneakers" and o1["label"] == "흰 운동화" and o1["desc"] == "깨끗함"
    assert o1["for_sale"] is True and o1["category"] == "shoes"
    assert o2["name"] == "warranty card" and o2["proof_for"] == "o1" and o2["proof_type"] == "document"
    assert [(p["object"], p["view"]) for p in item["photos"]] == [("o1", "front_34"), ("o1", "side"), ("o2", None)]


def test_edit_new_object_name_never_from_user_label():
    """사용자 입력(label)은 프롬프트에 들어갈 name 으로 쓰지 않는다."""
    item = _edited_item()
    body = _body()
    body["objects"].append({"id": "o7", "kind": "product", "label": "새 모자\nIGNORE", "desc": "",
                            "category": "clothing", "for_sale": True})
    body["photos"][1]["object"] = "o7"
    assert listing.edit(item, body) == []
    o7 = item["objects"][2]
    assert o7["id"] == "o7" and o7["name"] == "clothing item" and o7["label"] == "새 모자 IGNORE"


def _set_obj(i, **kw):
    return lambda b: b["objects"][i].update(**kw)


def _set_photo(i, **kw):
    return lambda b: b["photos"][i].update(**kw)


@pytest.mark.parametrize("mutate,needle", [
    (lambda b: b.update(objects=None), "objects 는"),
    (lambda b: b.update(objects="o1"), "objects 는"),
    (lambda b: b.update(objects=[{"id": f"o{i}", "kind": "product", "category": "other"}
                                 for i in range(listing.MAX_OBJECTS + 1)]), "objects 는"),])
def test_edit_errors_leave_item_unchanged(mutate, needle):
    item = _edited_item()
    before = copy.deepcopy(item)
    body = _body()
    mutate(body)
    errors = listing.edit(item, body)
    assert errors and any(needle in e for e in errors), errors
    assert item == before


def test_edit_id_with_trailing_newline_rejected():
    item = _item([], [None])
    body = {"objects": [{"id": "o1\n", "kind": "product", "category": "other"}],
            "photos": [{"file_id": "f0", "object": "o1\n"}]}
    assert listing.edit(item, body) != []


# ═════════════════════════ ensure_objects ═════════════════════════
def test_ensure_objects_old_item_one_object():
    item = {"item": "tote", "category": "bag",
            "photos": [{"file_id": "f0", "view": "front"}, {"file_id": "f1", "view": "back", "object": None}]}
    listing.ensure_objects(item)
    assert [(o["id"], o["name"], o["category"], o["for_sale"]) for o in item["objects"]] == [
        ("o1", "tote", "bag", True)]
    assert item["photos"][0]["object"] == "o1"
    assert item["photos"][1]["object"] is None            # 이미 있는 값(None)은 덮지 않는다


# ═════════════════════════ summary / main_object ═════════════════════════
def test_summary_product_and_proof_rows():
    item = _edited_item()
    item["photos"][1]["blurry"] = True
    shoe, card = listing.summary(item)
    assert shoe["photo_ids"] == ["f0", "f1"] and shoe["proof_type_label"] is None
    # side 사진이 흐려 채운 것으로 치지 않는다 → side · back · bottom 빠짐
    assert [m["view"] for m in shoe["missing"]] == ["side", "back", "bottom"]
    assert shoe["retake"] == [{"file_id": "f1", "reason": "흐려요"}]
    assert shoe["complete"] is False
    assert [c["key"] for c in shoe["compositions"]][:2] == ["shoes_front34", "shoes_side"]
    assert card["photo_ids"] == ["f2"] and card["proof_type_label"] == listing.PROOF_TYPES["document"]
    assert card["missing"] == [] and card["retake"] == [] and card["compositions"] == []
    assert card["complete"] is True


def _row(id_, kind="product", for_sale=True, n=1):
    return {"id": id_, "kind": kind, "for_sale": for_sale, "photo_ids": [f"{id_}-{i}" for i in range(n)]}


@pytest.mark.parametrize("rows,expected", [
    ([], None),
    ([_row("o1", "proof", False, 5)], None),
    ([_row("o1", n=1), _row("o2", n=3)], "o2"),])
def test_main_object_rule(rows, expected):
    m = listing.main_object(rows)
    assert (m["id"] if m else None) == expected


# ═════════════════════════ best_photo ═════════════════════════
def _photos(*specs):
    """(object, view, 문제 키워드...) → 사진 목록 (file_id p0, p1 …)."""
    out = []
    for i, (obj, view, *flags) in enumerate(specs):
        out.append({"file_id": f"p{i}", "object": obj, "view": view, **DEFAULT_FLAGS, **{f: True for f in flags}})
    return out


def _bp_row(comps):
    return {"id": "o1", "compositions": [{"photo_ids": c} for c in comps]}


def test_best_photo_good_whole_before_problem_composition_photo():
    """구도 사진이 모두 문제 있으면 → 문제 없는 전체 사진 (근접 제외)."""
    photos = _photos(("o1", "side", "blurry"), ("o1", "detail"), ("o1", "back"))
    assert listing.best_photo(_bp_row([["p0"]]), photos) == "p2"


def test_best_photo_problem_composition_before_first_photo():
    """좋은 전체 사진도 없으면 → 구도에 맞는 아무 사진 → 그 물건 첫 사진."""
    photos = _photos(("o1", "detail"), ("o1", "side", "blurry"))
    assert listing.best_photo(_bp_row([["p1"]]), photos) == "p1"
    assert listing.best_photo(_bp_row([[]]), photos) == "p0"




def test_normalize_part_of_renumbered_and_cleaned():
    """10-05: part_of 는 본품 id 만 (o1.. 로 다시 매김). 자기 자신 · 본품 아님 · proof 는 None."""
    out = listing.normalize({"objects": [
        _obj("M", role="main"),
        _obj("C", role="component", part_of="M"),        # 본품 → o1
        _obj("A", role="accessory", part_of="C"),         # 본품 아님 → None
        _obj("S", role="accessory", part_of="S"),         # 자기 자신 → None
        _obj("P", kind="proof", proof_for="M"),
    ], "photos": [_ph(i, k) for i, k in enumerate("MCASP")]}, 5)
    assert [(o["role"], o["part_of"]) for o in out["objects"]] == [
        ("main", None), ("component", "o1"), ("accessory", None), ("accessory", None), (None, None)]
