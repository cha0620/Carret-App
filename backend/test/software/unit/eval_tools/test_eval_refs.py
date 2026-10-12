"""eval/refs.py — 구도 예시 셋 검사 (load_refs · entry_errors · errors · style_needs · table · gaps · cmd_status).

10-11: 규칙 각도(compositions · coverage.REQUIRED) 대조를 지웠다 — 필요한 부위는 style_refs.json 의 needs 를 보여 준다.
"""
import json

import pytest

from app.services import coverage


@pytest.fixture
def fake(refs_mod, monkeypatch, tmp_path):
    """종류를 둘로 줄이고 모듈 경로(REFS_JSON · REFS_DIR · SVG_DIR)를 tmp_path 로."""
    monkeypatch.setattr(coverage, "CATEGORIES", ("shoes", "bag"))
    refs_dir = tmp_path / "data" / "refs"
    svg_dir = tmp_path / "svg"
    refs_dir.mkdir(parents=True)
    svg_dir.mkdir(parents=True)
    monkeypatch.setattr(refs_mod, "REFS_DIR", refs_dir)
    monkeypatch.setattr(refs_mod, "SVG_DIR", svg_dir)
    monkeypatch.setattr(refs_mod, "REFS_JSON", tmp_path / "data" / "refs.json")
    monkeypatch.setattr(refs_mod, "STYLE_REFS_JSON", tmp_path / "data" / "style_refs.json")
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


@pytest.mark.parametrize("key", ["new:", "Pair", "shoes-pair",])
def test_entry_bad_composition_name(fake, key):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    errs = mod.entry_errors(ref(composition=key), refs_dir)
    assert len(errs) == 1 and "구도 키는 영문 소문자 · 숫자 · _ 로" in errs[0]


def test_entry_old_new_prefix_is_read_as_name(fake):
    mod, refs_dir, _ = fake
    touch(refs_dir, "a.jpg")
    assert mod.entry_errors(ref(composition="new:pair"), refs_dir) == []


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


def test_table_groups_by_composition_and_merges_needs(fake):
    mod, refs_dir, _ = fake
    refs = refs_with_files(
        refs_dir,
        ref(file="1.jpg", composition="pair", view="front"),
        ref(file="2.jpg", composition="new:pair", view="side"),
        ref(file="3.jpg", composition="pair", view="bad"),                 # 각도 오류 — 세지 않는다
        ref(file="4.jpg", category="bag", composition="bag_front", view="front"),
    )
    t = mod.table(refs, refs_dir, {"1.jpg": ["앞코"], "2.jpg": ["앞코", "옆면"]})
    assert t["shoes"] == [{"key": "pair", "views": {"front", "side"}, "n": 2, "needs": ["앞코", "옆면"]}]
    assert t["bag"] == [{"key": "bag_front", "views": {"front"}, "n": 1, "needs": []}]


# ---------- gaps ----------


def test_gaps_examples_needs_svg(fake):
    mod, refs_dir, svg_dir = fake
    refs = refs_with_files(refs_dir, *(ref(file=f"{i}.jpg") for i in range(mod.MIN_EXAMPLES)),
                           ref(file="t.jpg", composition="s_top", view="top"))
    (svg_dir / "s_front.svg").write_text("<svg/>")
    g = mod.gaps(refs, refs_dir, svg_dir, {"0.jpg": ["앞코"]})
    assert not any(m.startswith("[shoes] s_front:") for m in g)
    assert f"[shoes] s_top: 예시 1/{mod.MIN_EXAMPLES}장" in g
    assert any(m.startswith("[shoes] s_top: 필요한 부위(needs) 없음") for m in g)
    assert any(m.startswith("[shoes] s_top: 선 그림 없음") for m in g)
    assert "[bag] 구도 예시가 없다" in g


# ---------- style_needs ----------


def test_style_needs_reads_file_and_tolerates_broken(fake):
    mod, *_ = fake
    assert mod.style_needs() == {}                                   # 파일 없음
    mod.STYLE_REFS_JSON.write_text(json.dumps([{"file": "a.jpg", "needs": ["밑창 전체", ""]}, {"file": "b.jpg"}, "x"]),
                                   encoding="utf-8")
    assert mod.style_needs() == {"a.jpg": ["밑창 전체"], "b.jpg": []}
    mod.STYLE_REFS_JSON.write_text("[{", encoding="utf-8")
    assert mod.style_needs() == {}


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
    """실제 coverage 정의로 status 가 오류 없이 0 (예시 0장)."""
    monkeypatch.setattr(refs_mod, "REFS_JSON", tmp_path / "none.json")
    monkeypatch.setattr(refs_mod, "REFS_DIR", tmp_path / "data" / "refs")
    assert refs_mod.cmd_status(None) == 0
    assert "예시 0장" in capsys.readouterr().out
