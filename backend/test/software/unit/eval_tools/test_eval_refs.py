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
    refs_dir = tmp_path / "data" / "refs"
    svg_dir = tmp_path / "svg"
    refs_dir.mkdir(parents=True)
    svg_dir.mkdir(parents=True)
    monkeypatch.setattr(refs_mod, "REFS_DIR", refs_dir)
    monkeypatch.setattr(refs_mod, "SVG_DIR", svg_dir)
    monkeypatch.setattr(refs_mod, "REFS_JSON", tmp_path / "data" / "refs.json")
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


def test_load_refs_broken_json_exits_with_message(fake):
    mod, *_ = fake
    mod.REFS_JSON.write_text("[{", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        mod.load_refs()
    assert "refs.json 를 읽지 못했다" in str(e.value)


# ---------- entry_errors ----------

def test_entry_ok(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.entry_errors(ref(), refs_dir) == []


def test_entry_photo_not_in_refs_dir(fake):
    mod, refs_dir, _ = fake
    assert mod.entry_errors(ref(), refs_dir) == ["a.jpg: refs/ 에 사진이 없다"]


@pytest.mark.parametrize("f", ["sub/a.jpg", "../a.jpg", "/abs/a.jpg",])
def test_entry_path_with_folder(fake, f, tmp_path):
    mod, refs_dir, _ = fake
    (refs_dir / "sub").mkdir()
    touch(refs_dir / "sub", "a.jpg")
    touch(tmp_path, "a.jpg")            # ../a.jpg 가 실제로 있어도
    errs = mod.entry_errors(ref(file=f), refs_dir)
    assert errs == [f"{f}: 파일 이름만 적는다 (폴더 · ../ 없이)"]


@pytest.mark.parametrize("name", ["", "Pair", "shoes-pair",])
def test_entry_new_composition_bad_name(fake, name):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    errs = mod.entry_errors(ref(composition=f"new:{name}"), refs_dir)
    assert len(errs) == 1 and "새 구도 이름은 영문 소문자 · 숫자 · _ 로" in errs[0]


def test_entry_composition_category_mismatch(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.entry_errors(ref(category="clothing"), refs_dir) == [
        "a.jpg: 구도 s_front 는 shoes 것인데 종류가 clothing"]


# ---------- errors ----------


def test_errors_duplicate_file(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.errors([ref(), ref()], refs_dir) == ["같은 파일이 두 번: a.jpg"]


def test_errors_same_photo_different_names(fake):
    mod, refs_dir, _ = fake
    (refs_dir / "a.jpg").write_bytes(b"same")
    (refs_dir / "b.jpg").write_bytes(b"same")
    (refs_dir / "c.jpg").write_bytes(b"other")
    refs = [ref(file="a.jpg"), ref(file="b.jpg"), ref(file="c.jpg")]
    assert mod.errors(refs, refs_dir) == ["같은 사진이 다른 이름으로: a.jpg · b.jpg"]


# ---------- valid ----------


# ---------- table ----------


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


def test_table_new_composition_count_excludes_bad_examples(fake):
    """각도가 틀린 예시는 n 에도 views 에도 안 들어간다."""
    mod, refs_dir, _ = fake
    refs = refs_with_files(refs_dir, ref(file="1.jpg", composition="new:pair", view="front"),
                           ref(file="2.jpg", composition="new:pair", view="bad"))
    row = next(r for r in mod.table(refs, refs_dir)["shoes"] if r["new"])
    assert row["n"] == 1 and row["views"] == {"front"}


# ---------- asked_for ----------


# ---------- gaps ----------


def test_gaps_min_examples_counts_only_valid(fake):
    mod, refs_dir, svg_dir = fake
    refs = refs_with_files(refs_dir, *(ref(file=f"{i}.jpg") for i in range(mod.MIN_EXAMPLES)),
                           ref(file="t.jpg", composition="s_top", view="top"))
    refs += [ref(file="bad.jpg", composition="s_top", view="top")]     # 사진 없음 — 세지 않는다
    g = mod.gaps(refs, refs_dir, svg_dir)
    assert not any("s_front: 예시" in m for m in g)
    assert f"[shoes] s_top: 예시 1/{mod.MIN_EXAMPLES}장" in g
    assert f"[clothing] c_front: 예시 0/{mod.MIN_EXAMPLES}장" in g


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


def test_gaps_new_composition_not_in_made(fake):
    """new: 구도가 bottom 으로 만들어도 아직 앱에 없으니 방향 2 경고는 그대로."""
    mod, refs_dir, svg_dir = fake
    refs = refs_with_files(refs_dir, ref(composition="new:sole", view="bottom"))
    g = mod.gaps(refs, refs_dir, svg_dir)
    assert any(m.startswith("[shoes] 꼭 받는 면 bottom(밑창)") for m in g)


# ---------- cmd_status / main ----------

def write_refs(mod, refs):
    mod.REFS_JSON.write_text(json.dumps(refs, ensure_ascii=False), encoding="utf-8")


def test_cmd_status_errors_return_1(fake, capsys):
    mod, *_ = fake
    write_refs(mod, [ref()])                                    # 사진 파일 없음
    assert mod.cmd_status(None) == 1
    out = capsys.readouterr().out
    assert "라벨 오류:" in out and "a.jpg: refs/ 에 사진이 없다" in out


def test_status_smoke_with_real_definitions(refs_mod, monkeypatch, tmp_path, capsys):
    """실제 compositions · coverage 정의로 status 가 오류 없이 0 (예시 0장)."""
    monkeypatch.setattr(refs_mod, "REFS_JSON", tmp_path / "none.json")
    monkeypatch.setattr(refs_mod, "REFS_DIR", tmp_path / "data" / "refs")
    assert refs_mod.cmd_status(None) == 0
    assert "예시 0장" in capsys.readouterr().out
