"""eval/refs.py — 구도 예시 셋 검사 (load_refs · entry_errors · errors · valid · table · asked_for · gaps · cmd_status).

COMPOSITIONS · REQUIRED 는 가짜 정의로 바꿔 두고 본다 — 실제 구도 정의가 바뀌어도 깨지지 않게.
실제 정의는 스모크 테스트 하나에서만 쓴다.
"""
import json

import pytest

from app.services import compositions, coverage

FAKE_COMPS = {
    "shoes": [
        {"key": "s_front", "label": "앞", "views": {"front_34", "front"}},   # shoes front_34 면을 꼭 받는다
        {"key": "s_top", "label": "위", "views": {"top"}},                  # top 은 업로드 때 안 받는다
    ],
    "clothing": [
        {"key": "c_front", "label": "앞판", "views": {"front", "front_34"}},
        {"key": "c_back", "label": "뒤판", "views": {"back"}},              # back 면의 대체 각도 rear_34 가 빠짐
    ],
}
FAKE_REQUIRED = {
    "shoes": [
        ("front_34", {"front_34", "front"}, "앞쪽 비스듬히"),
        ("bottom", {"bottom"}, "밑창"),                                     # 밑창으로 만드는 구도가 없다
    ],
    "clothing": [
        ("front", {"front", "front_34"}, "앞판"),
        ("back", {"back", "rear_34"}, "뒤판"),
    ],
    "bag": [
        ("front", {"front"}, "앞면"),                                       # 구도 정의 없음
    ],
}


@pytest.fixture
def fake(refs_mod, monkeypatch, tmp_path):
    """가짜 구도 · 필수 면 + 모듈 경로(REFS_JSON · REFS_DIR · SVG_DIR)를 tmp_path 로."""
    monkeypatch.setattr(compositions, "COMPOSITIONS", FAKE_COMPS)
    monkeypatch.setattr(compositions, "BY_KEY", {c["key"]: c for cs in FAKE_COMPS.values() for c in cs})
    monkeypatch.setattr(coverage, "REQUIRED", FAKE_REQUIRED)
    monkeypatch.setattr(coverage, "CATEGORIES", tuple(FAKE_REQUIRED))
    refs_dir = tmp_path / "refs"
    svg_dir = tmp_path / "svg"
    refs_dir.mkdir()
    svg_dir.mkdir()
    monkeypatch.setattr(refs_mod, "REFS_DIR", refs_dir)
    monkeypatch.setattr(refs_mod, "SVG_DIR", svg_dir)
    monkeypatch.setattr(refs_mod, "REFS_JSON", tmp_path / "refs.json")
    return refs_mod, refs_dir, svg_dir


def ref(file="a.jpg", category="shoes", composition="s_front", view="front_34"):
    return {"file": file, "category": category, "composition": composition, "view": view}


def touch(d, *names):
    """파일마다 내용을 달리 쓴다 — 같으면 sha1 중복 오류가 난다."""
    for n in names:
        (d / n).write_bytes(n.encode())


def refs_with_files(d, *refs):
    touch(d, *(r["file"] for r in refs))
    return list(refs)


# ---------- load_refs ----------

def test_load_refs_missing_file(fake):
    mod, *_ = fake
    assert mod.load_refs() == []


def test_load_refs_uses_module_constant(fake):
    mod, *_ = fake
    mod.REFS_JSON.write_text('[{"file": "a.jpg", "note": "흰 배경"}]', encoding="utf-8")
    assert mod.load_refs() == [{"file": "a.jpg", "note": "흰 배경"}]


def test_load_refs_explicit_path(refs_mod, tmp_path):
    p = tmp_path / "r.json"
    p.write_text("[]", encoding="utf-8")
    assert refs_mod.load_refs(p) == []


def test_load_refs_broken_json_exits_with_message(fake):
    mod, *_ = fake
    mod.REFS_JSON.write_text("[{", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        mod.load_refs()
    assert "refs.json 를 읽지 못했다" in str(e.value)


def test_load_refs_not_utf8_exits(fake):
    mod, *_ = fake
    mod.REFS_JSON.write_bytes(b"\xff\xfe[\x00]")
    with pytest.raises(SystemExit) as e:
        mod.load_refs()
    assert "읽지 못했다" in str(e.value)


@pytest.mark.parametrize("body", ['{"file": "a.jpg"}', '"x"', "3", "null"])
def test_load_refs_not_a_list_exits(fake, body):
    mod, *_ = fake
    mod.REFS_JSON.write_text(body, encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        mod.load_refs()
    assert "목록이어야 한다" in str(e.value)


# ---------- entry_errors ----------

def test_entry_ok(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.entry_errors(ref(), refs_dir) == []


def test_entry_default_refs_dir_is_module_constant(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.entry_errors(ref()) == []


@pytest.mark.parametrize("name", ["a.JPG", "a.jpeg", "a.png", "a.webp"])
def test_entry_image_extensions_case_insensitive(fake, name):
    mod, refs_dir, _ = fake
    touch(refs_dir, name)
    assert mod.entry_errors(ref(file=name), refs_dir) == []


@pytest.mark.parametrize("r", [None, "a.jpg", 3, ["a.jpg"]])
def test_entry_not_a_dict(fake, r):
    mod, refs_dir, _ = fake
    errs = mod.entry_errors(r, refs_dir)
    assert len(errs) == 1 and errs[0].startswith("항목이 {...} 가 아니다")


@pytest.mark.parametrize("f", [None, "", 3, ["a.jpg"], {"x": 1}])
def test_entry_file_missing_or_not_string(fake, f):
    mod, refs_dir, _ = fake
    errs = mod.entry_errors({"file": f, "category": "shoes"}, refs_dir)
    assert len(errs) == 1 and errs[0].startswith("file 이 없는 항목")


def test_entry_photo_not_in_refs_dir(fake):
    mod, refs_dir, _ = fake
    assert mod.entry_errors(ref(), refs_dir) == ["a.jpg: refs/ 에 사진이 없다"]


def test_entry_directory_is_not_a_photo(fake):
    mod, refs_dir, _ = fake
    (refs_dir / "a.jpg").mkdir()
    assert mod.entry_errors(ref(), refs_dir) == ["a.jpg: refs/ 에 사진이 없다"]


def test_entry_not_an_image(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.gif")
    assert mod.entry_errors(ref(file="a.gif"), refs_dir) == ["a.gif: 사진 파일이 아니다 (jpg · png · webp)"]


def test_entry_missing_and_not_image_both_reported(fake):
    mod, refs_dir, _ = fake
    assert mod.entry_errors(ref(file="a.txt"), refs_dir) == [
        "a.txt: refs/ 에 사진이 없다", "a.txt: 사진 파일이 아니다 (jpg · png · webp)"]


def test_entry_no_extension(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "photo")
    assert mod.entry_errors(ref(file="photo"), refs_dir) == ["photo: 사진 파일이 아니다 (jpg · png · webp)"]


@pytest.mark.parametrize("f", ["sub/a.jpg", "../a.jpg", "/abs/a.jpg", "a.jpg/"])
def test_entry_path_with_folder(fake, f, tmp_path):
    mod, refs_dir, _ = fake
    (refs_dir / "sub").mkdir()
    touch(refs_dir / "sub", "a.jpg")
    touch(tmp_path, "a.jpg")            # ../a.jpg 가 실제로 있어도
    errs = mod.entry_errors(ref(file=f), refs_dir)
    assert errs == [f"{f}: 파일 이름만 적는다 (폴더 · ../ 없이)"]


def test_entry_unknown_category_only_that_error(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    errs = mod.entry_errors(ref(category="toy"), refs_dir)
    assert len(errs) == 1 and errs[0].startswith("a.jpg: 모르는 종류 'toy'")


def test_entry_unknown_view_only_that_error(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    errs = mod.entry_errors(ref(view="diagonal"), refs_dir)
    assert len(errs) == 1 and errs[0].startswith("a.jpg: 모르는 각도 'diagonal'")


@pytest.mark.parametrize("field", ["category", "view", "composition"])
@pytest.mark.parametrize("value", [{"a": 1}, ["shoes"], 3, 1.5, True])
def test_entry_non_string_values_do_not_crash(fake, field, value):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    r = ref()
    r[field] = value
    errs = mod.entry_errors(r, refs_dir)
    assert len(errs) == 1
    expect = {"category": "모르는 종류", "view": "모르는 각도", "composition": "composition 칸이 비었다"}[field]
    assert expect in errs[0]


@pytest.mark.parametrize("comp", [None, ""])
def test_entry_empty_composition(fake, comp):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    r = ref(composition=comp)
    assert mod.entry_errors(r, refs_dir) == ["a.jpg: composition 칸이 비었다 (구도 키, 새 구도면 new:<이름>)"]
    del r["composition"]
    assert mod.entry_errors(r, refs_dir) == ["a.jpg: composition 칸이 비었다 (구도 키, 새 구도면 new:<이름>)"]


def test_entry_unknown_composition(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.entry_errors(ref(composition="nope"), refs_dir) == [
        "a.jpg: 모르는 구도 'nope' — 새 구도면 'new:<이름>'"]


@pytest.mark.parametrize("name", ["pair", "shoes_pair_2", "x", "0"])
def test_entry_new_composition_ok_skips_composition_checks(fake, name):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    # 새 구도는 종류 · 각도 대조를 하지 않는다
    assert mod.entry_errors(ref(composition=f"new:{name}", view="inside"), refs_dir) == []


@pytest.mark.parametrize("name", ["", "Pair", "shoes-pair", "shoes pair", "쌍", "a:b", "pair\n"])
def test_entry_new_composition_bad_name(fake, name):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    errs = mod.entry_errors(ref(composition=f"new:{name}"), refs_dir)
    assert len(errs) == 1 and "새 구도 이름은 영문 소문자 · 숫자 · _ 로" in errs[0]


def test_entry_new_composition_existing_key(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.entry_errors(ref(composition="new:s_top"), refs_dir) == [
        "a.jpg: s_top 는 이미 있는 구도 — new: 를 빼고 적는다"]


def test_entry_new_composition_still_checks_category_and_view(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    errs = mod.entry_errors(ref(category="toy", composition="new:x", view="bad"), refs_dir)
    assert len(errs) == 2
    assert errs[0].startswith("a.jpg: 모르는 종류") and errs[1].startswith("a.jpg: 모르는 각도")


def test_entry_composition_category_mismatch(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.entry_errors(ref(category="clothing"), refs_dir) == [
        "a.jpg: 구도 s_front 는 shoes 것인데 종류가 clothing"]


def test_entry_composition_view_mismatch(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.entry_errors(ref(category="clothing", composition="c_front", view="side"), refs_dir) == [
        "a.jpg: 구도 c_front 는 front · front_34 각도로 만드는데 예시 각도가 side"]


def test_entry_category_and_view_mismatch_both(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    errs = mod.entry_errors(ref(category="clothing", composition="s_top", view="side"), refs_dir)
    assert len(errs) == 2


def test_entry_bad_category_suppresses_mismatch_but_view_mismatch_stays(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    errs = mod.entry_errors(ref(category="toy", composition="s_top", view="side"), refs_dir)
    assert len(errs) == 2
    assert errs[0].startswith("a.jpg: 모르는 종류") and "예시 각도가 side" in errs[1]


# ---------- errors ----------

def test_errors_empty(fake):
    mod, refs_dir, _ = fake
    assert mod.errors([], refs_dir) == []


def test_errors_default_refs_dir_is_module_constant(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.errors([ref()]) == []


def test_errors_duplicate_file(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.errors([ref(), ref()], refs_dir) == ["같은 파일이 두 번: a.jpg"]


def test_errors_includes_entry_errors(fake):
    mod, refs_dir, _ = fake
    assert mod.errors([ref(), 7], refs_dir) == ["a.jpg: refs/ 에 사진이 없다", "항목이 {...} 가 아니다: 7"]


def test_errors_non_dict_and_bad_file_entries_do_not_crash(fake):
    mod, refs_dir, _ = fake
    errs = mod.errors([None, [], {"file": 3}, {"file": ["a"]}], refs_dir)
    assert len(errs) == 4


def test_errors_photo_only_in_refs_dir(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg", "b.png", "c.webp")
    errs = mod.errors([ref()], refs_dir)
    assert errs == ["refs.json 에 없는 사진: b.png", "refs.json 에 없는 사진: c.webp"]


def test_errors_ignores_non_image_files_and_subdirs_on_disk(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg", "README.txt", ".DS_Store")
    (refs_dir / "old").mkdir()
    touch(refs_dir / "old", "x.jpg")
    assert mod.errors([ref()], refs_dir) == []


def test_errors_same_photo_different_names(fake):
    mod, refs_dir, _ = fake
    (refs_dir / "a.jpg").write_bytes(b"same")
    (refs_dir / "b.jpg").write_bytes(b"same")
    (refs_dir / "c.jpg").write_bytes(b"other")
    refs = [ref(file="a.jpg"), ref(file="b.jpg"), ref(file="c.jpg")]
    assert mod.errors(refs, refs_dir) == ["같은 사진이 다른 이름으로: a.jpg · b.jpg"]


def test_errors_refs_dir_missing(fake, tmp_path):
    mod, *_ = fake
    errs = mod.errors([ref()], tmp_path / "nope")
    assert errs == ["a.jpg: refs/ 에 사진이 없다"]


# ---------- valid ----------

def test_valid_keeps_only_error_free(fake):
    mod, refs_dir, _ = fake
    good = refs_with_files(refs_dir, ref(file="1.jpg"), ref(file="2.jpg", composition="new:pair"))
    bad = [ref(file="missing.jpg"), ref(file="1.jpg", view="bad"), "x", {"file": None}]
    assert mod.valid(good + bad, refs_dir) == good


def test_valid_default_refs_dir(fake):
    mod, refs_dir, _ = fake
    good = refs_with_files(refs_dir, ref())
    assert mod.valid(good) == good


# ---------- table ----------

def test_table_lists_compositions_with_zero_examples(fake):
    mod, refs_dir, _ = fake
    t = mod.table([], refs_dir)
    assert set(t) == {"shoes", "clothing"}
    assert [r["key"] for r in t["shoes"]] == ["s_front", "s_top"]
    assert all(r["n"] == 0 and r["new"] is False for rows in t.values() for r in rows)
    assert t["clothing"][0]["views"] == {"front", "front_34"}


def test_table_counts_only_valid(fake):
    mod, refs_dir, _ = fake
    refs = refs_with_files(refs_dir, *(ref(file=f"{i}.jpg") for i in range(3)))
    refs += [ref(file="missing.jpg"),                           # 사진 없음
             ref(file="0.jpg", composition="s_front", view="top"),  # 각도 불일치
             ref(file="x.jpg", category="clothing")]             # 종류 불일치 (사진도 없음)
    t = mod.table(refs, refs_dir)
    assert {r["key"]: r["n"] for r in t["shoes"]} == {"s_front": 3, "s_top": 0}
    assert t["clothing"][0]["n"] == 0


def test_table_default_refs_dir(fake):
    mod, refs_dir, _ = fake
    refs = refs_with_files(refs_dir, ref())
    assert mod.table(refs)["shoes"][0]["n"] == 1


def test_table_views_are_copied_not_shared(fake):
    mod, refs_dir, _ = fake
    t = mod.table([], refs_dir)
    t["shoes"][0]["views"].add("side")
    assert FAKE_COMPS["shoes"][0]["views"] == {"front_34", "front"}


def test_table_aggregates_new_compositions(fake):
    mod, refs_dir, _ = fake
    refs = refs_with_files(
        refs_dir,
        ref(file="1.jpg", composition="new:pair", view="front"),
        ref(file="2.jpg", composition="new:pair", view="side"),
        ref(file="3.jpg", composition="new:pair", view="front"),
        ref(file="4.jpg", category="bag", composition="new:bag_front", view="front"),
    )
    t = mod.table(refs, refs_dir)
    new = [r for r in t["shoes"] if r["new"]]
    assert new == [{"key": "new:pair", "label": "(새 구도)", "views": {"front", "side"}, "n": 3, "new": True}]
    # 정의가 없는 종류도 새 구도가 있으면 나온다
    assert t["bag"] == [{"key": "new:bag_front", "label": "(새 구도)", "views": {"front"}, "n": 1, "new": True}]


def test_table_new_composition_same_name_in_two_categories_separate(fake):
    mod, refs_dir, _ = fake
    refs = refs_with_files(refs_dir, ref(file="1.jpg", composition="new:flat", view="top"),
                           ref(file="2.jpg", category="clothing", composition="new:flat", view="top"))
    t = mod.table(refs, refs_dir)
    assert [r["n"] for r in t["shoes"] if r["new"]] == [1]
    assert [r["n"] for r in t["clothing"] if r["new"]] == [1]


def test_table_new_composition_with_only_bad_examples_is_dropped(fake):
    mod, refs_dir, _ = fake
    refs = refs_with_files(refs_dir, ref(composition="new:pair", view="bad"))
    assert not any(r["new"] for r in mod.table(refs, refs_dir)["shoes"])


def test_table_new_composition_count_excludes_bad_examples(fake):
    """각도가 틀린 예시는 n 에도 views 에도 안 들어간다."""
    mod, refs_dir, _ = fake
    refs = refs_with_files(refs_dir, ref(file="1.jpg", composition="new:pair", view="front"),
                           ref(file="2.jpg", composition="new:pair", view="bad"))
    row = next(r for r in mod.table(refs, refs_dir)["shoes"] if r["new"])
    assert row["n"] == 1 and row["views"] == {"front"}


# ---------- asked_for ----------

@pytest.mark.parametrize("cat, views, expect", [
    ("shoes", {"front_34", "front"}, True),          # 한 면의 ok 전부
    ("shoes", {"front_34", "front", "top"}, True),   # 상위 집합도 OK
    ("shoes", {"front_34"}, False),                  # 대체 각도 하나만 겹침
    ("shoes", {"front"}, False),
    ("shoes", {"bottom"}, True),
    ("shoes", {"top"}, False),
    ("shoes", set(), False),
    ("clothing", {"back"}, False),
    ("clothing", {"back", "rear_34"}, True),
    ("unknown", {"front"}, False),
])
def test_asked_for(fake, cat, views, expect):
    mod, *_ = fake
    assert mod.asked_for(cat, views) is expect


def test_required_views_removed(refs_mod):
    assert not hasattr(refs_mod, "required_views")


# ---------- gaps ----------

def all_svgs(svg_dir, *keys):
    touch(svg_dir, *(f"{k}.svg" for k in keys))


def test_gaps_min_examples_counts_only_valid(fake):
    mod, refs_dir, svg_dir = fake
    refs = refs_with_files(refs_dir, *(ref(file=f"{i}.jpg") for i in range(mod.MIN_EXAMPLES)),
                           ref(file="t.jpg", composition="s_top", view="top"))
    refs += [ref(file="bad.jpg", composition="s_top", view="top")]     # 사진 없음 — 세지 않는다
    g = mod.gaps(refs, refs_dir, svg_dir)
    assert not any("s_front: 예시" in m for m in g)
    assert f"[shoes] s_top: 예시 1/{mod.MIN_EXAMPLES}장" in g
    assert f"[clothing] c_front: 예시 0/{mod.MIN_EXAMPLES}장" in g


def test_gaps_min_examples_boundary(fake, monkeypatch):
    mod, refs_dir, svg_dir = fake
    monkeypatch.setattr(mod, "MIN_EXAMPLES", 2)
    refs = refs_with_files(refs_dir, ref(file="1.jpg"), ref(file="2.jpg"))
    assert not any("s_front: 예시" in m for m in mod.gaps(refs, refs_dir, svg_dir))
    assert "[shoes] s_front: 예시 1/2장" in mod.gaps(refs[:1], refs_dir, svg_dir)


def test_gaps_defaults_are_module_constants(fake):
    mod, refs_dir, svg_dir = fake
    all_svgs(svg_dir, "s_front", "s_top", "c_front", "c_back")
    refs = refs_with_files(refs_dir, ref())
    g = mod.gaps(refs)
    assert not any("선 그림 없음" in m for m in g)
    assert "[shoes] s_front: 예시 1/3장" in g


def test_gaps_all_categories_even_without_refs(fake):
    mod, refs_dir, svg_dir = fake
    g = mod.gaps([], refs_dir, svg_dir)
    assert "[bag] compositions.py 에 정석 구도가 없다 — 사진은 정리만 된다 (예시가 모이면 구도를 정한다)" in g
    assert not any(m.startswith("[shoes] compositions.py 에 정석 구도가 없다") for m in g)
    # 구도 정의 없는 종류는 방향 2 를 생략
    assert not any(m.startswith("[bag] 꼭 받는 면") for m in g)


def test_gaps_unknown_category_ref_ignored(fake):
    mod, refs_dir, svg_dir = fake
    refs = refs_with_files(refs_dir, ref(category="toy", composition="new:x", view="front"))
    assert not any(m.startswith("[toy]") for m in mod.gaps(refs, refs_dir, svg_dir))


def test_gaps_new_composition_hint_only(fake):
    mod, refs_dir, svg_dir = fake
    refs = refs_with_files(refs_dir, ref(file="1.jpg", composition="new:pair", view="side"),
                           ref(file="2.jpg", composition="new:pair", view="inside"))
    g = mod.gaps(refs, refs_dir, svg_dir)
    hits = [m for m in g if "new:pair" in m]
    assert hits == [f"[shoes] new:pair: 예시 2/{mod.MIN_EXAMPLES}장",
                    "[shoes] new:pair: compositions.py 에 추가할 새 구도 (각도 inside · side)"]


def test_gaps_category_with_only_new_compositions(fake):
    mod, refs_dir, svg_dir = fake
    refs = refs_with_files(refs_dir, ref(category="bag", composition="new:bag_front", view="front"))
    g = mod.gaps(refs, refs_dir, svg_dir)
    assert any(m.startswith("[bag] compositions.py 에 정석 구도가 없다") for m in g)
    assert any(m.startswith("[bag] new:bag_front: compositions.py 에 추가할 새 구도") for m in g)
    assert not any(m.startswith("[bag] 꼭 받는 면") for m in g)


def test_gaps_missing_svg(fake):
    mod, refs_dir, svg_dir = fake
    all_svgs(svg_dir, "s_front", "c_front", "c_back")
    g = mod.gaps([], refs_dir, svg_dir)
    assert [m for m in g if "선 그림 없음" in m] == [
        "[shoes] s_top: 선 그림 없음 (frontend/img/compositions/s_top.svg)"]


def test_gaps_direction1_not_asked_at_upload(fake):
    mod, refs_dir, svg_dir = fake
    g = mod.gaps([], refs_dir, svg_dir)
    hit = [m for m in g if "업로드 때 꼭 받지 않는다" in m]
    assert [m.split(":")[0] for m in hit] == ["[shoes] s_top", "[clothing] c_back"]
    assert hit[0].startswith("[shoes] s_top: 각도 top 를 업로드 때 꼭 받지 않는다")


def test_gaps_direction1_partial_overlap_warns(fake, monkeypatch):
    """구도 각도가 필수 면의 대체 각도 하나만 겹치면 경고한다."""
    mod, refs_dir, svg_dir = fake
    comps = {"shoes": [{"key": "s_front", "label": "앞", "views": {"front_34"}}]}
    monkeypatch.setattr(compositions, "COMPOSITIONS", comps)
    monkeypatch.setattr(compositions, "BY_KEY", {"s_front": comps["shoes"][0]})
    g = mod.gaps([], refs_dir, svg_dir)
    assert any(m.startswith("[shoes] s_front: 각도 front_34 를 업로드 때 꼭 받지 않는다") for m in g)


def test_gaps_direction2_required_face_without_composition(fake):
    mod, refs_dir, svg_dir = fake
    g = mod.gaps([], refs_dir, svg_dir)
    d2 = [m for m in g if "꼭 받는 면" in m]
    assert d2 == [
        "[shoes] 꼭 받는 면 bottom(밑창) 를 bottom 사진으로 채우면 그걸로 만드는 구도가 없다 — 그 사진은 정리만 된다",
        "[clothing] 꼭 받는 면 back(뒤판) 를 rear_34 사진으로 채우면 그걸로 만드는 구도가 없다 — 그 사진은 정리만 된다",
    ]


def test_gaps_direction2_lists_all_lacking_views_sorted(fake, monkeypatch):
    mod, refs_dir, svg_dir = fake
    req = dict(FAKE_REQUIRED, shoes=[("back", {"rear_34", "back", "side"}, "뒤")])
    monkeypatch.setattr(coverage, "REQUIRED", req)
    g = mod.gaps([], refs_dir, svg_dir)
    assert any(m.startswith("[shoes] 꼭 받는 면 back(뒤) 를 back · rear_34 · side 사진으로") for m in g)


def test_gaps_new_composition_not_in_made(fake):
    """new: 구도가 bottom 으로 만들어도 아직 앱에 없으니 방향 2 경고는 그대로."""
    mod, refs_dir, svg_dir = fake
    refs = refs_with_files(refs_dir, ref(composition="new:sole", view="bottom"))
    g = mod.gaps(refs, refs_dir, svg_dir)
    assert any(m.startswith("[shoes] 꼭 받는 면 bottom(밑창)") for m in g)


def test_gaps_new_composition_not_asked_check_skipped(fake):
    mod, refs_dir, svg_dir = fake
    refs = refs_with_files(refs_dir, ref(composition="new:inner", view="inside"))
    g = mod.gaps(refs, refs_dir, svg_dir)
    assert not any("new:inner" in m and "업로드" in m for m in g)


def test_gaps_invalid_refs_ignored(fake):
    """라벨이 틀린 예시는 새 구도로도 안 잡힌다."""
    mod, refs_dir, svg_dir = fake
    g = mod.gaps([ref(composition="new:ghost", view="top")], refs_dir, svg_dir)   # 사진 없음
    assert not any("new:ghost" in m for m in g)


# ---------- cmd_status / main ----------

def write_refs(mod, refs):
    mod.REFS_JSON.write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")


def test_cmd_status_ok_returns_0(fake, capsys):
    mod, refs_dir, _ = fake
    write_refs(mod, refs_with_files(refs_dir, ref()))
    assert mod.cmd_status(None) == 0
    out = capsys.readouterr().out
    assert "예시 1장" in out and "## shoes" in out and "s_front" in out and " 1장" in out
    assert "라벨 오류:" not in out
    assert "할 일:" in out          # 예시 부족 등은 오류가 아니다


def test_cmd_status_errors_return_1(fake, capsys):
    mod, *_ = fake
    write_refs(mod, [ref()])                                    # 사진 파일 없음
    assert mod.cmd_status(None) == 1
    out = capsys.readouterr().out
    assert "라벨 오류:" in out and "a.jpg: refs/ 에 사진이 없다" in out


def test_cmd_status_stray_photo_returns_1(fake, capsys):
    mod, refs_dir, _ = fake
    touch(refs_dir, "stray.jpg")
    assert mod.cmd_status(None) == 1
    assert "refs.json 에 없는 사진: stray.jpg" in capsys.readouterr().out


def test_cmd_status_no_refs_json_returns_0(fake):
    mod, *_ = fake
    assert mod.cmd_status(None) == 0


def test_cmd_status_broken_json_exits(fake):
    mod, *_ = fake
    mod.REFS_JSON.write_text("{oops", encoding="utf-8")
    with pytest.raises(SystemExit):
        mod.cmd_status(None)


def test_main_dispatches_status(fake, monkeypatch):
    mod, *_ = fake
    monkeypatch.setattr(mod, "cmd_status", lambda a: 7)
    assert mod.main(["status"]) == 7


def test_main_requires_subcommand(refs_mod):
    with pytest.raises(SystemExit):
        refs_mod.main([])


def test_status_smoke_with_real_definitions(refs_mod, monkeypatch, tmp_path, capsys):
    """실제 compositions · coverage 정의로 status 가 오류 없이 0 (예시 0장)."""
    monkeypatch.setattr(refs_mod, "REFS_JSON", tmp_path / "none.json")
    monkeypatch.setattr(refs_mod, "REFS_DIR", tmp_path / "refs")
    assert refs_mod.cmd_status(None) == 0
    assert "예시 0장" in capsys.readouterr().out
