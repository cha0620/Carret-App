"""services/listing — 여러 장 업로드를 물건 단위로 (10-04).

normalize(VLM 응답 정리) · apply(분류 결과 합치기, 사용자가 고친 묶음 지키기) · edit(사용자 수정 검사)
· ensure_objects(옛 묶음) · summary / main_object(물건별 빠진 면 · 대표 물건). 순수 함수라 VLM · storage 없이 본다.
"""
import copy

import pytest

from app.services import coverage, listing

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


def _one(**kw):
    """물건 하나 + 그 물건 사진 한 장 → 정리된 물건 (사진 없는 물건은 버려지므로 꼭 붙인다)."""
    return listing.normalize({"objects": [_obj("A", **kw)], "photos": [_ph(0, "A")]}, 1)["objects"][0]


# ═════════════════════════ normalize ═════════════════════════
def test_normalize_basic_two_objects_ids_renumbered():
    out = listing.normalize({
        "objects": [_obj("A", name="sneakers", label="흰 운동화", category="shoes"),
                    _obj("B", name="bag", category="bag")],
        "photos": [_ph(0, "A", "front_34"), _ph(1, "B", "front"), _ph(2, "A", "bottom")],
    }, 3)
    assert out["objects"][0] == {"id": "o1", "kind": "product", "name": "sneakers", "label": "흰 운동화",
                                 "desc": "", "proof_type": None, "proof_for": None, "category": "shoes",
                                 "subtype": None, "for_sale": True, "count": 1}
    assert [o["id"] for o in out["objects"]] == ["o1", "o2"]
    assert [p["object"] for p in out["photos"]] == ["o1", "o2", "o1"]
    assert [p["view"] for p in out["photos"]] == ["front_34", "front", "bottom"]
    assert all({k: p[k] for k in DEFAULT_FLAGS} == DEFAULT_FLAGS for p in out["photos"])


def test_normalize_ids_follow_object_order_not_letters():
    out = listing.normalize({"objects": [_obj("Z"), _obj("A")],
                             "photos": [_ph(0, "A"), _ph(1, "Z")]}, 2)
    assert [p["object"] for p in out["photos"]] == ["o2", "o1"]


@pytest.mark.parametrize("ids,refs", [
    ([1, 2], [2, "1"]),                       # 숫자 id · 문자열 참조
    (["A", "B"], ["b", " a "]),               # 대소문자 · 공백
])
def test_normalize_id_matching_is_loose(ids, refs):
    out = listing.normalize({"objects": [_obj(ids[0]), _obj(ids[1])],
                             "photos": [_ph(0, refs[0]), _ph(1, refs[1])]}, 2)
    assert [p["object"] for p in out["photos"]] == ["o2", "o1"]


def test_normalize_duplicate_id_first_wins():
    out = listing.normalize({"objects": [_obj("A", name="first"), _obj("a", name="second"), _obj("B")],
                             "photos": [_ph(0, "A"), _ph(1, "B")]}, 2)
    assert [o["name"] for o in out["objects"]] == ["first", "thing"]
    assert [o["id"] for o in out["objects"]] == ["o1", "o2"]


def test_normalize_objects_without_id_duplicate_dropped():
    """id 가 없는 물건 둘은 같은 키로 겹쳐 둘째가 버려진다."""
    out = listing.normalize({"objects": [_obj(None, name="x"), _obj(None, name="y"), _obj("B")],
                             "photos": [_ph(0, "B"), _ph(1, "x")]}, 2)
    assert "y" not in [o["name"] for o in out["objects"]]


def test_normalize_null_photo_not_attached_to_idless_object():
    out = listing.normalize({"objects": [_obj(None, name="noid"), _obj("B")],
                             "photos": [_ph(0, "B"), _ph(1, None)]}, 3)
    assert [p["object"] for p in out["photos"]][1:] == [None, None]


@pytest.mark.parametrize("kind", ["thing", None, "PRODUCT", "", 3, "proofs"])
def test_normalize_unknown_kind_becomes_product(kind):
    out = listing.normalize({"objects": [_obj("A", kind=kind, category="bag")],
                             "photos": [_ph(0, "A", "front")]}, 1)
    assert out["objects"][0]["kind"] == "product" and out["objects"][0]["category"] == "bag"
    assert out["photos"][0]["view"] == "front"


def test_normalize_proof_fields_forced():
    out = listing.normalize({"objects": [
        _obj("A", category="electronics"),
        _obj("B", kind="proof", proof_type="Document", proof_for="a", category="shoes", for_sale=True, count=5),
    ], "photos": [_ph(0, "A", "front"), _ph(1, "B", "front")]}, 2)
    proof = out["objects"][1]
    assert proof["kind"] == "proof" and proof["proof_type"] == "document"
    assert proof["category"] is None and proof["for_sale"] is False and proof["count"] == 1
    assert proof["proof_for"] == "o1"                       # 새 id 로 다시 가리킨다
    assert out["photos"][1]["view"] is None                # 근거 사진엔 각도 없음
    assert out["photos"][0]["view"] == "front"


@pytest.mark.parametrize("pt,expected", [
    ("internals", "internals"), (" SCREEN ", "screen"), ("receipt", "other"), (None, "other"), ("", "other"),
])
def test_normalize_proof_type(pt, expected):
    assert _one(kind="proof", proof_type=pt)["proof_type"] == expected


def test_normalize_proof_for_must_be_product():
    out = listing.normalize({"objects": [
        _obj("A", kind="proof", proof_for="B"),            # B 도 근거 사진 → None
        _obj("B", kind="proof", proof_for="B"),            # 자기 자신 → None
        _obj("C", kind="proof", proof_for="X"),            # 없는 물건 → None
        _obj("D", kind="proof", proof_for="P"),            # 상품 → 연결
        _obj("P"),
    ], "photos": [_ph(i, k) for i, k in enumerate("ABCDP")]}, 5)
    assert [o["proof_for"] for o in out["objects"]] == [None, None, None, "o5", None]


def test_normalize_proof_for_product_without_photos_cleared():
    """가리키던 상품이 사진이 없어 버려지면 근거 사진의 연결도 끊는다."""
    out = listing.normalize({"objects": [_obj("A"), _obj("B", kind="proof", proof_for="A")],
                             "photos": [_ph(0, "B")]}, 1)
    assert [o["id"] for o in out["objects"]] == ["o2"] and out["objects"][0]["proof_for"] is None


def test_normalize_product_proof_for_dropped():
    out = listing.normalize({"objects": [_obj("A", proof_for="B", proof_type="screen"), _obj("B")],
                             "photos": [_ph(0, "A"), _ph(1, "B")]}, 2)
    assert out["objects"][0]["proof_for"] is None and out["objects"][0]["proof_type"] is None


def test_normalize_objects_without_photos_dropped():
    out = listing.normalize({"objects": [_obj("A"), _obj("B", name="unused"), _obj("C")],
                             "photos": [_ph(0, "A"), _ph(1, "C")]}, 2)
    assert [o["id"] for o in out["objects"]] == ["o1", "o3"]       # id 는 다시 매기지 않는다
    assert [p["object"] for p in out["photos"]] == ["o1", "o3"]


@pytest.mark.parametrize("bad", [3, 99, -1, "1", 1.0, None, True, False])
def test_normalize_bad_index_ignored(bad):
    out = listing.normalize({"objects": [_obj("A")],
                             "photos": [_ph(0, "A", "side"),
                                        {"index": bad, "object": "A", "view": "front", "blurry": True}]}, 3)
    assert out["photos"][0]["view"] == "side"
    assert out["photos"][1:] == [{"object": None, "view": None, **DEFAULT_FLAGS}] * 2


def test_normalize_true_index_does_not_hit_photo_one():
    out = listing.normalize({"objects": [_obj("A")],
                             "photos": [{"index": True, "object": "A", "view": "back"},
                                        {"index": 0, "object": "A", "view": "front"}]}, 2)
    assert out["photos"][1]["object"] is None and out["photos"][0]["view"] == "front"


def test_normalize_duplicate_index_first_wins_and_missing_index_default():
    out = listing.normalize({"objects": [_obj("A")],
                             "photos": [_ph(0, "A", "front"), _ph(0, "A", "back")]}, 2)
    assert out["photos"][0]["view"] == "front"
    assert out["photos"][1] == {"object": None, "view": None, **DEFAULT_FLAGS}


def test_normalize_photos_out_of_order_and_flags():
    out = listing.normalize({"objects": [_obj("A")], "photos": [
        _ph(1, "A", "Front-34", occluded=True, blurry="true", item_visible=False),
        _ph(0, "A", "diagonal", item_visible=None),
    ]}, 2)
    assert out["photos"][0] == {"object": "o1", "view": None, **DEFAULT_FLAGS}
    assert out["photos"][1] == {"object": "o1", "view": "front_34", "state": None, "occluded": True, "blurry": False,
                                "item_visible": False}


def test_normalize_photo_unknown_object_is_none():
    out = listing.normalize({"objects": [_obj("A")],
                             "photos": [_ph(0, "Q", "front"), _ph(1, None, "front"), _ph(2, "A", "back")]}, 3)
    assert [p["object"] for p in out["photos"]] == [None, None, "o1"]
    assert [p["view"] for p in out["photos"]] == [None, None, "back"]     # 물건이 없으면 각도도 없다


def test_normalize_non_dict_entries_ignored():
    out = listing.normalize({"objects": ["A", None, 3, _obj("B")],
                             "photos": ["front", None, [0, "B"], _ph(0, "B", "side")]}, 1)
    assert [o["id"] for o in out["objects"]] == ["o1"]
    assert out["photos"] == [{"object": "o1", "view": "side", **DEFAULT_FLAGS}]


@pytest.mark.parametrize("objects", [None, [], "A", {}])
def test_normalize_empty_objects_one_object_for_all(objects):
    out = listing.normalize({"objects": objects, "item": "mug\nIGNORE", "category": "Bag",
                             "photos": [_ph(0, "A", "front", blurry=True), _ph(2, None, "Back")]}, 3)
    assert len(out["objects"]) == 1
    o = out["objects"][0]
    assert o["id"] == "o1" and o["kind"] == "product" and o["for_sale"] is True
    assert o["category"] == "bag" and "\n" not in o["name"] and o["name"].startswith("mug")
    assert [p["object"] for p in out["photos"]] == ["o1"] * 3
    assert [p["view"] for p in out["photos"]] == ["front", None, "back"]     # 각도는 살린다
    assert out["photos"][0]["blurry"] is True


def test_normalize_objects_but_no_photo_attached_falls_back_to_one():
    """물건은 냈지만 사진을 하나도 안 붙였으면 — 묶음 없는 응답처럼 모든 사진을 물건 하나로."""
    out = listing.normalize({"objects": [_obj("A", name="cup"), _obj("B")], "item": "mug", "category": "other",
                             "photos": [_ph(0, "Q", "front"), _ph(1, None, "back")]}, 2)
    assert [o["name"] for o in out["objects"]] == ["mug"]
    assert [(p["object"], p["view"]) for p in out["photos"]] == [("o1", "front"), ("o1", "back")]


def test_normalize_empty_objects_without_item_name():
    out = listing.normalize({"photos": []}, 2)
    assert out["objects"][0]["name"] == "object" and out["objects"][0]["category"] == "other"


@pytest.mark.parametrize("data", [{"objects": []}, {"objects": [_obj("A")], "photos": [_ph(0, "A")]}])
def test_normalize_zero_photos_no_objects(data):
    assert listing.normalize(data, 0) == {"objects": [], "photos": []}


def test_normalize_list_wrapped_response():
    out = listing.normalize([{"objects": [_obj("A", name="tote")], "photos": [_ph(0, "A", "inside")]},
                             {"objects": []}], 1)
    assert out["objects"][0]["name"] == "tote" and out["photos"][0]["view"] == "inside"


@pytest.mark.parametrize("resp", [[], ["x"], "text", 3, None, [[{"objects": []}]], [None, {}]])
def test_normalize_non_dict_raises_value_error(resp):
    with pytest.raises(ValueError, match="objects"):
        listing.normalize(resp, 1)


@pytest.mark.parametrize("field", ["objects", "photos"])
def test_normalize_non_iterable_field_raises(field):
    """objects · photos 가 숫자면 TypeError — 호출부(_classify)가 모든 예외를 "묶지 못함"으로 처리한다."""
    with pytest.raises(TypeError):
        listing.normalize({field: 5}, 1)


def test_normalize_text_fields_cleaned_and_cut():
    o = _one(name='n"' * 50, label="가\n나" + "다" * 60, desc='첫 줄\n둘째 줄\t"따옴표"')
    assert len(o["name"]) == 60 and '"' not in o["name"]          # name 은 프롬프트용 — 따옴표 무력화
    assert len(o["label"]) == listing.LABEL_MAX and o["label"].startswith("가 나")
    assert o["desc"] == '첫 줄 둘째 줄 "따옴표"'                  # 화면용 — 따옴표는 그대로


def test_normalize_desc_up_to_desc_max():
    assert len(_one(desc="가" * 150)["desc"]) == 150
    assert len(_one(desc="가" * 500)["desc"]) == listing.DESC_MAX


@pytest.mark.parametrize("name,label,exp_name,exp_label", [
    (None, None, "object", "object"), ("", "", "object", "object"),
    ("cup", None, "cup", "cup"), ("cup", "  ", "cup", "cup"), (None, "컵", "object", "컵"),
])
def test_normalize_name_label_fallbacks(name, label, exp_name, exp_label):
    o = _one(name=name, label=label)
    assert (o["name"], o["label"]) == (exp_name, exp_label)


def test_normalize_desc_none_is_empty():
    assert _one(desc=None)["desc"] == ""


@pytest.mark.parametrize("val,expected", [
    (False, False), ("false", False), (" No ", False), ("0", False), (0, False),
    (True, True), (None, True), ("true", True), (1, True), ("yes", True), ([], True),
])
def test_normalize_for_sale(val, expected):
    assert _one(for_sale=val)["for_sale"] is expected


@pytest.mark.parametrize("val,expected", [
    (None, 1), (2, 2), ("3", 3), (0, 1), (-5, 1), (1000, listing.MAX_COUNT), ("many", 1), (2.7, 2),
])
def test_normalize_count_clamped(val, expected):
    assert _one(count=val)["count"] == expected


@pytest.mark.parametrize("cat,expected", [("Shoes", "shoes"), ("toy", "other"), (None, "other"), (5, "other")])
def test_normalize_category(cat, expected):
    assert _one(category=cat)["category"] == expected


def test_normalize_max_objects():
    n_all = listing.MAX_OBJECTS + 5
    objs = [_obj(f"X{i}") for i in range(n_all)]
    out = listing.normalize({"objects": objs, "photos": [_ph(i, f"X{i}") for i in range(n_all)]}, n_all)
    assert len(out["objects"]) == listing.MAX_OBJECTS
    assert out["objects"][-1]["id"] == f"o{listing.MAX_OBJECTS}"
    assert out["photos"][listing.MAX_OBJECTS]["object"] is None    # 잘린 물건을 가리킨 사진은 모름


def test_normalize_photo_count_follows_n():
    out = listing.normalize({"objects": [_obj("A")], "photos": [_ph(i, "A") for i in range(5)]}, 2)
    assert len(out["photos"]) == 2


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


def test_apply_not_edited_missing_flag_treated_as_not_edited():
    item = {"photos": [{"file_id": "f0"}]}
    listing.apply(item, _out([_mine("o1")], [_aph("o1", "front")]), {"f0"})
    assert item["objects"][0]["id"] == "o1" and item["photos"][0]["view"] == "front"


def test_apply_pops_unclassified_flag():
    item = _item([_mine("o1")], ["o1", None])
    item["photos"][1]["unclassified"] = True
    listing.apply(item, _out([_mine("o1")], [_aph("o1"), _aph("o1", "back")]), set())
    assert all("unclassified" not in p for p in item["photos"])


def test_apply_edited_keeps_old_photos_and_objects():
    mine = [_mine("o1", label="내 컵"), _mine("o2", label="내 접시")]
    item = _item(copy.deepcopy(mine), ["o2", "o1"], edited=True, views=["side", "front"])
    before = copy.deepcopy(item["photos"])
    out = _out([_mine("o1", label="AI 하나")], [_aph("o1", "back", blurry=True), _aph("o1", "top")])
    listing.apply(item, out, set())
    assert item["photos"] == before and item["objects"] == mine
    assert "needs_review" not in item                       # 새 사진이 없으면 다시 확인할 것도 없다


def test_apply_edited_new_photo_follows_majority_vote():
    """AI 가 새 사진을 기존 사진 2장(내 o2)·1장(내 o1)과 같이 묶었으면 → 내 o2."""
    item = _item([_mine("o1"), _mine("o2")], ["o2", "o2", "o1", None], edited=True)
    item["photos"][3]["file_id"] = "new"
    out = _out([_mine("o1")], [_aph("o1"), _aph("o1"), _aph("o1"), _aph("o1", "back")])
    listing.apply(item, out, {"new"})
    assert item["photos"][3]["object"] == "o2" and item["photos"][3]["view"] == "back"
    assert [o["id"] for o in item["objects"]] == ["o1", "o2"]
    assert item["needs_review"] is True


def test_apply_edited_vote_tie_goes_to_first_seen():
    item = _item([_mine("o1"), _mine("o2")], ["o2", "o1", None], edited=True)
    item["photos"][2]["file_id"] = "new"
    out = _out([_mine("o1")], [_aph("o1"), _aph("o1"), _aph("o1", "front")])
    listing.apply(item, out, {"new"})
    assert item["photos"][2]["object"] == "o2"


def test_apply_edited_old_photo_without_object_does_not_vote():
    item = _item([_mine("o1")], [None, None, "o1", None], edited=True)
    item["photos"][3]["file_id"] = "new"
    out = _out([_mine("o1"), _mine("o2")], [_aph("o2"), _aph("o2"), _aph("o1"), _aph("o2", "front")])
    listing.apply(item, out, {"new"})
    # AI o2 는 물건이 정해진 기존 사진과 묶이지 않았다 → 새 물건
    assert item["photos"][3]["object"] == "o2"
    assert [o["id"] for o in item["objects"]] == ["o1", "o2"]
    assert [p["object"] for p in item["photos"][:2]] == [None, None]     # 기존 사진은 그대로


def test_apply_edited_unclassified_photo_treated_as_new():
    """지난 묶기가 실패해 "모름"이 된 사진은 다음 분류 때 새 사진처럼 붙는다."""
    item = _item([_mine("o1")], ["o1", None], edited=True)
    item["photos"][1]["unclassified"] = True
    listing.apply(item, _out([_mine("o1")], [_aph("o1"), _aph("o1", "back")]), set())
    assert item["photos"][1]["object"] == "o1" and item["photos"][1]["view"] == "back"
    assert "unclassified" not in item["photos"][1] and item["needs_review"] is True


def test_apply_edited_unmatched_ai_object_added_with_fresh_id():
    mine = [_mine("o1"), _mine("o3")]
    item = _item(mine, ["o1", "o3", None, None, None], edited=True)
    for i in (2, 3, 4):
        item["photos"][i]["file_id"] = f"new{i}"
    out = _out([_mine("o1"), _mine("o2", label="새 신발", category="shoes"), _mine("o3", label="또 새것")],
               [_aph("o1"), _aph("o1"), _aph("o2", "side"), _aph("o2", "back"), _aph("o3", "front")])
    listing.apply(item, out, {"new2", "new3", "new4"})
    assert [o["id"] for o in item["objects"]] == ["o1", "o3", "o2", "o4"]    # 비어 있는 가장 작은 번호
    assert item["objects"][2]["label"] == "새 신발" and item["objects"][2]["category"] == "shoes"
    assert item["objects"][3]["label"] == "또 새것"
    assert [p["object"] for p in item["photos"][2:]] == ["o2", "o2", "o4"]   # 같은 AI 물건은 한 번만 추가
    assert [p["view"] for p in item["photos"][2:]] == ["side", "back", "front"]


def test_apply_edited_respects_max_objects():
    mine = [_mine(f"o{i}") for i in range(1, listing.MAX_OBJECTS + 1)]
    item = _item(mine, [f"o{i}" for i in range(1, listing.MAX_OBJECTS + 1)] + [None], edited=True)
    item["photos"][-1]["file_id"] = "new"
    out = _out([_mine("o1"), _mine("o2")], [_aph("o1")] * listing.MAX_OBJECTS + [_aph("o2", "front")])
    listing.apply(item, out, {"new"})
    assert len(item["objects"]) == listing.MAX_OBJECTS
    assert item["photos"][-1]["object"] is None and item["photos"][-1]["view"] is None


def test_apply_edited_new_photo_ai_object_none():
    item = _item([_mine("o1")], ["o1", None], edited=True)
    item["photos"][1]["file_id"] = "new"
    out = _out([_mine("o1")], [_aph("o1"), _aph(None, None, blurry=True)])
    listing.apply(item, out, {"new"})
    assert item["photos"][1]["object"] is None and item["photos"][1]["view"] is None
    assert item["photos"][1]["blurry"] is True and len(item["objects"]) == 1


def test_apply_edited_new_photo_into_my_proof_has_no_view():
    """AI 는 상품으로 봤어도 내가 근거 사진으로 고친 물건에 붙으면 각도는 없다."""
    item = _item([_mine("o1"), _mine("o2", kind="proof", proof_for="o1")], ["o1", "o2", None], edited=True)
    item["photos"][2]["file_id"] = "new"
    out = _out([_mine("o1"), _mine("o2")], [_aph("o1"), _aph("o2", "front"), _aph("o2", "front")])
    listing.apply(item, out, {"new"})
    assert item["photos"][2]["object"] == "o2" and item["photos"][2]["view"] is None
    assert item["objects"][1]["proof_for"] == "o1"


def test_apply_edited_new_ai_proof_links_to_my_product():
    """새 근거 사진(AI o1)이 AI o2 를 보증 — AI o2 는 내 o5 로 묶였으니 내 o5 를 가리킨다 (id 가 안 겹치는 경우)."""
    item = _item([_mine("o5", category="shoes")], ["o5", None], edited=True)
    item["photos"][1]["file_id"] = "new"
    ai = [{**_mine("o1", kind="proof"), "proof_for": "o2"}, _mine("o2", category="shoes")]
    listing.apply(item, _out(ai, [_aph("o2"), _aph("o1")]), {"new"})
    added = item["objects"][1]
    assert added["id"] == "o1" and added["kind"] == "proof" and added["proof_for"] == "o5"
    assert item["photos"][1]["object"] == "o1" and item["photos"][1]["view"] is None


def test_apply_edited_new_ai_proof_links_common_ids():
    item = _item([_mine("o1", category="shoes")], ["o1", None], edited=True)
    item["photos"][1]["file_id"] = "new"
    # AI: o1 = 다른 물건(사진 없음), o2 = 운동화(내 o1 과 같은 사진), o3 = 운동화 보증서(새 사진).
    # 새 물건은 비어 있는 가장 작은 번호 o2 를 받고, proof_for "o2"(AI 운동화)가 자기 자신으로 읽혀 None 이 된다.
    ai = [_mine("o1"), _mine("o2", category="shoes"), {**_mine("o3", kind="proof"), "proof_for": "o2"}]
    listing.apply(item, _out(ai, [_aph("o2"), _aph("o3")]), {"new"})
    assert item["objects"][1]["proof_for"] == "o1"


def test_apply_edited_new_ai_proof_link_with_colliding_ids():
    # 내 o1 = 컵(f0), o2 = 운동화(f1). AI 는 o1 = 운동화, o2 = 컵, o3 = 운동화 보증서(새 사진 f2)
    item = _item([_mine("o1", name="cup"), _mine("o2", name="shoes", category="shoes")], ["o1", "o2", None],
                 edited=True)
    ai = [_mine("o1", name="shoes", category="shoes"), _mine("o2", name="cup"),
          {**_mine("o3", kind="proof", name="card"), "proof_for": "o1"}]
    listing.apply(item, _out(ai, [_aph("o2"), _aph("o1"), _aph("o3")]), {"f2"})
    card = next(o for o in item["objects"] if o["name"] == "card")
    assert card["proof_for"] == "o2"                       # 내 운동화


def test_apply_edited_fixes_dangling_proof_links():
    item = _item([_mine("o1", kind="proof"), _mine("o2")], ["o1", "o2"], edited=True)
    item["objects"][0]["proof_for"] = "o9"
    listing.apply(item, _out([_mine("o1")], [_aph("o1"), _aph("o1")]), set())
    assert item["objects"][0]["proof_for"] is None


def test_apply_edited_without_objects_key():
    item = {"photos": [{"file_id": "a", "object": None}, {"file_id": "b"}], "user_edited": True}
    listing.apply(item, _out([_mine("o1")], [_aph("o1", "front"), _aph("o1", "back")]), {"b"})
    assert [o["id"] for o in item["objects"]] == ["o1"]
    assert item["photos"][1]["object"] == "o1" and item["photos"][1]["view"] == "back"


def test_apply_does_not_mutate_ai_objects_on_add():
    item = _item([_mine("o1")], ["o1", None], edited=True)
    item["photos"][1]["file_id"] = "new"
    ai_obj = {**_mine("o1", kind="proof"), "proof_for": "o5"}
    out = _out([_mine("o9"), ai_obj], [_aph(None), _aph("o1")])
    listing.apply(item, out, {"new"})
    assert ai_obj["proof_for"] == "o5" and ai_obj["id"] == "o1"


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


@pytest.mark.parametrize("change,expected", [
    ({"category": "bag"}, "bag"),                                    # 종류가 바뀌면 영어 이름도 종류에서
    ({"category": "clothing"}, "clothing item"),
    ({"category": "other"}, "item"),
    ({"kind": "proof", "proof_type": "screen"}, "screen"),
    ({"kind": "proof", "proof_type": None}, "photo"),
])
def test_edit_name_follows_kind_category_change(change, expected):
    item = _edited_item()
    body = _body()
    body["objects"][0].update(change)
    assert listing.edit(item, body) == []
    assert item["objects"][0]["name"] == expected


def test_edit_proof_type_change_renames():
    item = _edited_item()
    body = _body()
    body["objects"][1]["proof_type"] = "internals"
    assert listing.edit(item, body) == []
    assert item["objects"][1]["name"] == "internal parts"


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


def test_edit_new_object_empty_label_falls_back_to_name():
    item = _edited_item()
    body = _body()
    body["objects"].append({"id": "o3", "kind": "product", "label": "", "category": "bag", "for_sale": True})
    body["photos"][1]["object"] = "o3"
    assert listing.edit(item, body) == []
    assert item["objects"][2]["name"] == "bag" and item["objects"][2]["label"] == "bag"


@pytest.mark.parametrize("val,expected", [(True, True), (False, False), ("yes", False), (1, False), (None, False)])
def test_edit_for_sale_must_be_true_exactly(val, expected):
    item = _edited_item()
    body = _body()
    body["objects"][0]["for_sale"] = val
    assert listing.edit(item, body) == []
    assert item["objects"][0]["for_sale"] is expected


@pytest.mark.parametrize("val,expected", [(3, 3), (None, 1), (0, 1), (500, listing.MAX_COUNT)])
def test_edit_count(val, expected):
    item = _edited_item()
    body = _body()
    body["objects"][0]["count"] = val
    assert listing.edit(item, body) == []
    assert item["objects"][0]["count"] == expected


def test_edit_proof_forced_not_for_sale_and_no_category():
    item = _edited_item()
    body = _body()
    body["objects"][1].update(for_sale=True, category="shoes", count=4)
    assert listing.edit(item, body) == []
    o2 = item["objects"][1]
    assert o2["for_sale"] is False and o2["category"] is None and o2["count"] == 1


def test_edit_proof_type_missing_becomes_other():
    item = _edited_item()
    body = _body()
    body["objects"][1]["proof_type"] = None
    assert listing.edit(item, body) == []
    assert item["objects"][1]["proof_type"] == "other"


def test_edit_change_product_to_proof_drops_views():
    item = _edited_item()
    body = _body()
    body["objects"][0].update(kind="proof", proof_type="screen")
    assert listing.edit(item, body) == []
    assert [p["view"] for p in item["photos"]] == [None, None, None]
    assert item["objects"][1]["proof_for"] is None           # o1 은 이제 상품이 아니다


def test_edit_long_desc_kept_up_to_desc_max():
    item = _edited_item()
    body = _body()
    body["objects"][0]["desc"] = "가" * 150
    assert listing.edit(item, body) == []
    assert len(item["objects"][0]["desc"]) == 150


def test_edit_object_without_photos_removed_and_proof_for_cleared():
    item = _edited_item()
    body = _body(photos=[{"file_id": "f0", "object": "o2", "view": "front"},
                         {"file_id": "f1", "object": None, "view": "side"},
                         {"file_id": "f2", "object": "o2", "view": None}])
    assert listing.edit(item, body) == []
    assert [o["id"] for o in item["objects"]] == ["o2"]
    assert item["objects"][0]["proof_for"] is None           # 가리키던 상품이 지워졌다
    assert [(p["object"], p["view"]) for p in item["photos"]] == [("o2", None), (None, None), ("o2", None)]


def test_edit_proof_for_proof_cleared():
    item = _edited_item()
    body = _body()
    body["objects"].append({"id": "o3", "kind": "proof", "proof_type": "screen", "proof_for": "o2"})
    body["photos"][1]["object"] = "o3"
    assert listing.edit(item, body) == []
    assert item["objects"][2]["proof_for"] is None


def test_edit_photo_order_in_body_does_not_matter():
    item = _edited_item()
    body = _body()
    body["photos"].reverse()
    assert listing.edit(item, body) == []
    assert [p["file_id"] for p in item["photos"]] == ["f0", "f1", "f2"]
    assert item["photos"][0]["view"] == "front_34"


def test_edit_all_photos_unassigned_leaves_no_objects():
    item = _edited_item()
    body = _body(photos=[{"file_id": f"f{i}", "object": None, "view": None} for i in range(3)])
    assert listing.edit(item, body) == []
    assert item["objects"] == [] and item["user_edited"] is True


def test_edit_view_on_unassigned_photo_dropped():
    item = _edited_item()
    body = _body()
    body["photos"][1] = {"file_id": "f1", "object": None, "view": "side"}
    assert listing.edit(item, body) == []
    assert item["photos"][1]["view"] is None


def test_edit_keeps_photo_flags():
    item = _edited_item()
    item["photos"][0]["blurry"] = True
    assert listing.edit(item, _body()) == []
    assert item["photos"][0]["blurry"] is True and item["photos"][0]["source"] == "photo"


def _set_obj(i, **kw):
    return lambda b: b["objects"][i].update(**kw)


def _set_photo(i, **kw):
    return lambda b: b["photos"][i].update(**kw)


@pytest.mark.parametrize("mutate,needle", [
    (lambda b: b.update(objects=None), "objects 는"),
    (lambda b: b.update(objects="o1"), "objects 는"),
    (lambda b: b.update(objects=[{"id": f"o{i}", "kind": "product", "category": "other"}
                                 for i in range(listing.MAX_OBJECTS + 1)]), "objects 는"),
    (lambda b: b["objects"].append("o3"), "객체가 아니다"),
    (_set_obj(0, id="A"), "물건 id"),
    (_set_obj(0, id="o"), "물건 id"),
    (_set_obj(0, id="o1234"), "물건 id"),
    (_set_obj(0, id=" o1"), "물건 id"),
    (_set_obj(0, id="O1"), "물건 id"),
    (_set_obj(0, id=1), "물건 id"),
    (_set_obj(0, id=None), "물건 id"),
    (_set_obj(1, id="o1"), "물건 id"),                     # 겹침
    (_set_obj(0, kind="thing"), "kind"),
    (_set_obj(0, kind=None), "kind"),
    (_set_obj(0, category="toy"), "category"),
    (_set_obj(0, category=None), "category"),
    (_set_obj(0, category="Shoes"), "category"),
    (_set_obj(1, proof_type="receipt"), "proof_type"),
    (_set_obj(1, proof_type="Document"), "proof_type"),
    (lambda b: b.update(photos=None), "photos 는 목록"),
    (lambda b: b.update(photos={"file_id": "f0"}), "photos 는 목록"),
    (_set_photo(0, file_id="nope"), "이 묶음에 없거나"),
    (lambda b: b["photos"].append({"file_id": "f0", "object": "o1", "view": None}), "두 번"),
    (lambda b: b["photos"].append("f0"), "이 묶음에 없거나"),
    (lambda b: b["photos"].pop(), "사진이 빠졌다"),
    (lambda b: b.update(photos=[]), "사진이 빠졌다"),
    (_set_photo(0, object="o9"), "없는 물건"),
    (_set_photo(0, object="A"), "없는 물건"),
    (_set_photo(0, view="diagonal"), "view"),
    (_set_photo(0, view="Front"), "view"),
])
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


def test_edit_photo_refers_to_rejected_object_reports_both():
    item = _edited_item()
    body = _body()
    body["objects"][0]["kind"] = "thing"
    errors = listing.edit(item, body)
    assert any("kind" in e for e in errors) and any("없는 물건 'o1'" in e for e in errors)


def test_edit_missing_photo_not_reported_when_other_errors():
    item = _edited_item()
    body = _body(photos=[{"file_id": "f0", "object": "o1", "view": "bad"}])
    errors = listing.edit(item, body)
    assert len(errors) == 1 and "view" in errors[0]


def test_edit_max_objects_exactly_allowed():
    item = _item([], [None])
    objs = [{"id": f"o{i}", "kind": "product", "category": "other", "label": f"{i}"}
            for i in range(1, listing.MAX_OBJECTS + 1)]
    assert listing.edit(item, {"objects": objs, "photos": [{"file_id": "f0", "object": "o20"}]}) == []
    assert [o["id"] for o in item["objects"]] == ["o20"]


def test_edit_three_digit_id_allowed():
    item = _item([], [None])
    body = {"objects": [{"id": "o999", "kind": "product", "category": "bag"}],
            "photos": [{"file_id": "f0", "object": "o999", "view": "inside"}]}
    assert listing.edit(item, body) == []
    assert item["photos"][0]["view"] == "inside"


# ═════════════════════════ ensure_objects ═════════════════════════
def test_ensure_objects_old_item_one_object():
    item = {"item": "tote", "category": "bag",
            "photos": [{"file_id": "f0", "view": "front"}, {"file_id": "f1", "view": "back", "object": None}]}
    listing.ensure_objects(item)
    assert [(o["id"], o["name"], o["category"], o["for_sale"]) for o in item["objects"]] == [
        ("o1", "tote", "bag", True)]
    assert item["photos"][0]["object"] == "o1"
    assert item["photos"][1]["object"] is None            # 이미 있는 값(None)은 덮지 않는다


@pytest.mark.parametrize("item", [
    {"photos": [{"file_id": "f0"}], "objects": []},
    {"photos": [{"file_id": "f0", "object": "o2"}], "objects": [{"id": "o2"}]},
    {"photos": []},
    {"photos": [], "objects": None},
])
def test_ensure_objects_noop(item):
    before = copy.deepcopy(item)
    listing.ensure_objects(item)
    assert item == before


def test_ensure_objects_missing_item_and_category():
    item = {"photos": [{"file_id": "f0"}]}
    listing.ensure_objects(item)
    assert item["objects"][0]["name"] == "object" and item["objects"][0]["category"] == "other"


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


def test_summary_proof_photo_problems_not_retake():
    item = _edited_item()
    item["photos"][2].update(blurry=True, item_visible=False)
    card = listing.summary(item)[1]
    assert card["retake"] == [] and card["complete"] is True


def test_summary_product_without_photos_misses_everything():
    item = _item([_mine("o1", category="bag")], [None])
    row = listing.summary(item)[0]
    assert row["photo_ids"] == [] and len(row["missing"]) == len(coverage.REQUIRED["bag"])


def test_summary_no_objects_key_is_empty():
    assert listing.summary({"photos": [{"file_id": "f0", "view": "front"}]}) == []


def test_summary_keeps_object_fields():
    row = listing.summary(_edited_item())[0]
    assert {k: row[k] for k in ("id", "kind", "name", "label", "category", "for_sale", "count")} == {
        "id": "o1", "kind": "product", "name": "sneakers", "label": "운동화", "category": "shoes",
        "for_sale": True, "count": 1}


def _row(id_, kind="product", for_sale=True, n=1):
    return {"id": id_, "kind": kind, "for_sale": for_sale, "photo_ids": [f"{id_}-{i}" for i in range(n)]}


@pytest.mark.parametrize("rows,expected", [
    ([], None),
    ([_row("o1", "proof", False, 5)], None),
    ([_row("o1", n=1), _row("o2", n=3)], "o2"),
    ([_row("o1", n=2), _row("o2", n=2)], "o1"),                               # 같으면 앞
    ([_row("o1", n=2), _row("o2", n=3), _row("o3", n=3)], "o2"),
    ([_row("o1", for_sale=False, n=9), _row("o2", n=1)], "o2"),               # 파는 것 먼저
    ([_row("o1", "proof", False, 9), _row("o2", for_sale=False, n=1), _row("o3", for_sale=False, n=2)], "o3"),
    ([_row("o1", for_sale=False, n=2), _row("o2", for_sale=False, n=2)], "o1"),
    ([_row("o1", "proof", False, 9), _row("o2", n=0)], "o2"),
])
def test_main_object_rule(rows, expected):
    m = listing.main_object(rows)
    assert (m["id"] if m else None) == expected


def test_main_object_with_summary_rows():
    item = _item([_mine("o1"), _mine("o2", for_sale=False), _mine("o3")], ["o1", "o2", "o2", "o3", "o3"])
    assert listing.main_object(listing.summary(item))["id"] == "o3"


# ═════════════════════════ best_photo ═════════════════════════
def _photos(*specs):
    """(object, view, 문제 키워드...) → 사진 목록 (file_id p0, p1 …)."""
    out = []
    for i, (obj, view, *flags) in enumerate(specs):
        out.append({"file_id": f"p{i}", "object": obj, "view": view, **DEFAULT_FLAGS, **{f: True for f in flags}})
    return out


def _bp_row(comps):
    return {"id": "o1", "compositions": [{"photo_ids": c} for c in comps]}


def test_best_photo_first_ok_composition_photo():
    photos = _photos(("o1", "front"), ("o1", "side", "blurry"), ("o1", "back"), ("o1", "top"))
    row = _bp_row([[], ["p1", "p3"], ["p2"]])
    assert listing.best_photo(row, photos) == "p3"                  # p1 은 흐려 건너뛴다


def test_best_photo_good_whole_before_problem_composition_photo():
    """구도 사진이 모두 문제 있으면 → 문제 없는 전체 사진 (근접 제외)."""
    photos = _photos(("o1", "side", "blurry"), ("o1", "detail"), ("o1", "back"))
    assert listing.best_photo(_bp_row([["p0"]]), photos) == "p2"


def test_best_photo_problem_composition_before_first_photo():
    """좋은 전체 사진도 없으면 → 구도에 맞는 아무 사진 → 그 물건 첫 사진."""
    photos = _photos(("o1", "detail"), ("o1", "side", "blurry"))
    assert listing.best_photo(_bp_row([["p1"]]), photos) == "p1"
    assert listing.best_photo(_bp_row([[]]), photos) == "p0"


def test_best_photo_composition_photo_blurry_skipped_with_summary():
    item = _item([_mine("o1", category="shoes")], ["o1", "o1"], views=["side", "side"])
    item["photos"][1]["blurry"] = True
    row = listing.summary(item)[0]
    assert listing.best_photo(row, item["photos"]) == "f0"
    item["photos"][0]["blurry"], item["photos"][1]["blurry"] = True, False
    assert listing.best_photo(listing.summary(item)[0], item["photos"]) == "f1"


def test_best_photo_shoes_front34_first():
    item = _item([_mine("o1", category="shoes")], ["o1", "o1", "o1"], views=["bottom", "side", "front_34"])
    row = listing.summary(item)[0]
    assert listing.best_photo(row, item["photos"]) == "f2"


@pytest.mark.parametrize("specs,expected", [
    # 구도 없음 → 문제 없고 근접(label/detail)이 아닌 첫 사진
    ([("o1", "label"), ("o1", "detail"), ("o1", "back", "blurry"), ("o1", "side")], "p3"),
    ([("o2", "front"), ("o1", "front", "occluded"), ("o1", None)], "p2"),       # view 없어도 문제만 없으면
    ([("o1", "front", "item_visible_false")], "p0"),
    # 좋은 사진이 없으면 그 물건 첫 사진
    ([("o2", "front"), ("o1", "detail"), ("o1", "front", "blurry")], "p1"),
    # 물건 사진이 없으면 None
    ([("o2", "front")], None),
    ([], None),
])
def test_best_photo_fallbacks(specs, expected):
    photos = _photos(*specs)
    for p in photos:
        if p.pop("item_visible_false", None):
            p["item_visible"] = False
    row = {"id": "o1", "compositions": []}
    assert listing.best_photo(row, photos) == expected


def test_best_photo_item_not_visible_is_problem():
    photos = _photos(("o1", "front"), ("o1", "back"))
    photos[0]["item_visible"] = False
    assert listing.best_photo({"id": "o1"}, photos) == "p1"


def test_best_photo_without_compositions_key():
    assert listing.best_photo({"id": "o1"}, _photos(("o1", "front"))) == "p0"
