"""eval/intake.py — inbox → dataset.json, split 배정, 라벨 CSV 왕복."""
import csv
import json
from argparse import Namespace
from pathlib import Path


def _entry(file, stratum="none", split="test", labeled=False, **kw):
    e = {"file": file, "stratum": stratum, "split": split, "item": "", "photo_type": "",
         "wear_level": "", "text_level": "", "key_texts": [], "note": "", "labeled_by": "",
         "ambiguous": False}
    if labeled:
        e.update(photo_type="product", wear_level="none", text_level="none")
    e.update(kw)
    return e


def _put(path, data: bytes):
    path.write_bytes(data)
    return path


# ── add ──
def test_add_moves_files_with_urls_and_splits(intake_mod, capsys):
    m = intake_mod
    _put(m.INBOX / "none_a.jpg", b"a")
    _put(m.INBOX / "doc_b.png", b"b")
    (m.INBOX / "urls.txt").write_text(
        "# 주석 줄\n\nnone_a.jpg  https://x/1 \n# doc_b.png https://nope\n", encoding="utf-8")

    assert m.cmd_add(Namespace()) == 0
    entries = {e["file"]: e for e in m.load_dataset()}
    assert set(entries) == {"none_a.jpg", "doc_b.png"}
    assert entries["none_a.jpg"]["url"] == "https://x/1"
    assert entries["doc_b.png"]["url"] == ""               # 주석 처리된 줄은 무시
    assert entries["doc_b.png"]["stratum"] == "doc"
    assert all(e["split"] in ("dev", "test") for e in entries.values())
    assert (m.IMAGES / "none_a.jpg").exists() and not (m.INBOX / "none_a.jpg").exists()
    assert m.LABELS_CSV.exists()
    assert "urls.txt 에 주소가 없는 게시글 1개: doc_b.png" in capsys.readouterr().out


def test_add_is_all_or_nothing_on_any_error(intake_mod, capsys):
    m = intake_mod
    _put(m.IMAGES / "none_old.jpg", b"same")
    m.save_dataset([_entry("none_old.jpg")])
    _put(m.INBOX / "none_ok.jpg", b"fresh")
    _put(m.INBOX / "cat_x.jpg", b"x")                     # 모르는 층 코드
    _put(m.INBOX / "noprefix.jpg", b"y")                  # _ 없음
    _put(m.INBOX / "none_dup.jpg", b"same")               # 이미 있는 사진과 내용이 같다
    before = m.DATASET.read_text(encoding="utf-8")

    assert m.cmd_add(Namespace()) == 1
    out = capsys.readouterr().out
    assert "cat_x.jpg" in out and "noprefix.jpg" in out
    assert "none_dup.jpg: 첫 사진이 none_old.jpg 와 같은 사진이다" in out
    assert "none_ok.jpg" not in out
    assert m.DATASET.read_text(encoding="utf-8") == before
    assert (m.INBOX / "none_ok.jpg").exists() and not (m.IMAGES / "none_ok.jpg").exists()


def test_plan_add_duplicate_name_and_duplicate_within_inbox(intake_mod, tmp_path):
    m = intake_mod
    _put(m.IMAGES / "none_on_disk.jpg", b"disk")          # dataset 에 없어도 images/ 에 있으면 충돌
    entries = [_entry("none_known.jpg")]                   # dataset 에 있고 파일은 없음
    files = [_put(m.INBOX / "none_known.jpg", b"1"), _put(m.INBOX / "none_on_disk.jpg", b"2"),
             _put(m.INBOX / "none_p.jpg", b"twin"), _put(m.INBOX / "none_q.jpg", b"twin")]

    new, errors = m.plan_add(files, {}, entries, m.IMAGES)
    assert [e["file"] for e in new] == ["none_p.jpg"]
    assert sorted(errors) == sorted([
        "none_known.jpg: 같은 이름이 이미 있다",
        "none_on_disk.jpg: 같은 이름이 이미 있다",
        "none_q.jpg: 첫 사진이 none_p.jpg 와 같은 사진이다"])          # 파일 하나 게시글 = 첫 사진


# ── split ──
def _new(n, stratum="none"):
    return [{"file": f"{stratum}_{i:03d}.jpg", "stratum": stratum} for i in range(n)]


def test_assign_splits_follows_strata_ratio_from_empty(intake_mod):
    new = _new(30)                                         # none: test 25 · dev 5
    intake_mod.assign_splits([], new)
    assert sum(e["split"] == "dev" for e in new) == 5
    assert sum(e["split"] == "test" for e in new) == 25


def test_assign_splits_incremental_keeps_existing_and_ends_at_quota(intake_mod):
    """조금씩 add 해도 기존 split 은 그대로이고, 목표만큼 채우면 dev·test 가 정확히 목표 수."""
    m = intake_mod
    entries = []
    for batch in (_new(1), [{"file": f"none_b{i}.jpg", "stratum": "none"} for i in range(6)],
                  [{"file": f"none_c{i}.jpg", "stratum": "none"} for i in range(23)]):
        frozen = [(e["file"], e["split"]) for e in entries]
        m.assign_splits(entries, batch)
        assert [(e["file"], e["split"]) for e in entries] == frozen   # 기존 split 불변
        entries += batch
    assert entries[0]["split"] == m.split_slots("none")[0]           # 첫 사진 = 첫 자리
    assert sum(e["split"] == "dev" for e in entries) == 5            # 목표 수를 다 채우면 정확히 목표


def test_split_slots_spread_dev_and_are_fixed_per_stratum(intake_mod):
    m = intake_mod
    slots = m.split_slots("none")
    assert slots == m.split_slots("none") and len(slots) == 30
    assert slots.count("dev") == 5
    assert slots != ["dev"] * 5 + ["test"] * 25                   # 앞쪽(먼저 모은 사진)에 몰리지 않는다
    assert m.split_slots("wear").count("dev") == 8


# ── 라벨 CSV ──
def _read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def test_labels_csv_round_trip_merge(intake_mod):
    m = intake_mod
    entries = [_entry("none_a.jpg", item="가방"), _entry("doc_b.jpg", stratum="doc"),
               _entry("none_done.jpg", labeled=True)]
    assert m.write_labels_csv(entries, m.LABELS_CSV) == 2          # 라벨 된 항목은 빠진다
    rows = _read_csv(m.LABELS_CSV)
    assert [r["file"] for r in rows] == ["none_a.jpg", "doc_b.jpg"]
    assert rows[0]["item"] == "가방"
    rows[0].update(category="shoes", photo_type="product", wear_level="light", text_level="simple",
                   key_texts=" LV | Paris ||", ambiguous="Y", note="n")
    # rows[1] 은 비워 둔다 → 아직 안 단 줄, 오류 아님

    n, errors, warnings = m.merge_labels(entries, rows, "철수")
    assert (n, errors, warnings) == (1, [], [])
    a = entries[0]
    assert a["key_texts"] == ["LV", "Paris"]
    assert a["ambiguous"] is True and a["labeled_by"] == "철수" and a["note"] == "n"
    assert m.is_labeled(a)
    assert entries[1]["labeled_by"] == "" and not m.is_labeled(entries[1])


def test_merge_invalid_or_unknown_rows_change_nothing(intake_mod):
    m = intake_mod
    entries = [_entry("none_a.jpg"), _entry("none_b.jpg")]
    snapshot = [dict(e) for e in entries]
    good = {"file": "none_a.jpg", "category": "shoes", "photo_type": "product", "wear_level": "none", "text_level": "none"}
    rows = [good,
            {"file": "none_b.jpg", "photo_type": "product", "wear_level": "worn", "text_level": "none",
             "ambiguous": "maybe"},
            {"file": "ghost.jpg", "photo_type": "product", "wear_level": "none", "text_level": "none"}]

    n, errors, _ = m.merge_labels(entries, rows, "x")
    assert n == 0
    assert any("none_b.jpg: wear_level='worn'" in e for e in errors)
    assert any("none_b.jpg: ambiguous='maybe'" in e for e in errors)
    assert any("ghost.jpg: dataset.json 에 없다" in e for e in errors)
    assert entries == snapshot                              # 좋은 줄도 반영 안 됨


def test_cmd_merge_writes_dataset_only_when_valid(intake_mod, tmp_path, capsys):
    m = intake_mod
    m.save_dataset([_entry("none_a.jpg")])
    csv_path = tmp_path / "in.csv"
    with open(csv_path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=m.LABEL_COLS)
        w.writeheader()
        w.writerow({"file": "none_a.jpg", "category": "media", "photo_type": "document", "wear_level": "none",
                    "text_level": "dense"})
    assert m.main(["merge", str(csv_path), "--by", "영희"]) == 0
    e = m.load_dataset()[0]
    assert (e["photo_type"], e["labeled_by"]) == ("document", "영희")
    assert "1개 반영 (라벨 남은 항목 0개)" in capsys.readouterr().out


# ── dataset.json · status ──
def test_save_load_round_trip_one_entry_per_line(intake_mod):
    m = intake_mod
    entries = [_entry("none_a.jpg", item="한글 \"따옴표\""), _entry("doc_b.jpg", key_texts=["a", "b"])]
    m.save_dataset(entries)
    lines = m.DATASET.read_text(encoding="utf-8").splitlines()
    assert lines[0] == "[" and lines[-1] == "]" and len(lines) == 2 + len(entries)
    assert "한글" in lines[1]                                # ensure_ascii=False
    assert m.load_dataset() == entries
    m.save_dataset([])
    assert m.load_dataset() == []


def test_cmd_status_counts_and_flags_mismatches(intake_mod, capsys):
    m = intake_mod
    m.save_dataset([_entry("none_1.jpg", split="test", labeled=True),
                    _entry("none_2.jpg", split="test"),
                    _entry("none_3.jpg", split="dev"),
                    {"file": "old.webp", "photo_type": "product"}])           # 층·split 없는 옛 항목 → dev
    for name in ("none_1.jpg", "none_2.jpg", "old.webp", "stray.jpg"):
        _put(m.IMAGES / name, name.encode())                                  # none_3 은 없음, stray 는 dataset 에 없음
    m.SPLIT_LOG.write_text('{"file": "none_2.jpg", "split": "dev"}\n', encoding="utf-8")
    for name in ("none_x.jpg", "doc_y.png", "bad.jpg", "notes.txt"):
        _put(m.INBOX / name, b"x" + name.encode())

    assert m.cmd_status(Namespace()) == 0
    out = capsys.readouterr().out
    assert "| none | 2/25 | 1/5 | 1 | 2 |" in out
    assert "| doc | 0/15 | 0/4 | 1 | 0 |" in out
    assert "| (층 목표 밖 · 기존·파일럿) | 0 | 1 | | 1 |" in out
    assert "이름이 규칙에 안 맞는 inbox 사진: bad.jpg" in out
    assert "사진이 아닌 파일 (jpg · png · webp 만 받는다): notes.txt" in out
    assert "images/ 에 없다: none_3.jpg" in out
    assert "dataset.json 에 없는 사진: stray.jpg" in out
    assert "split 이 바뀌었다: none_2.jpg (dev → test)" in out


# ── 리뷰에서 나온 경우들 ──
def test_add_keeps_labels_already_filled_in_csv(intake_mod):
    """라벨을 반쯤 달다가 사진을 더 모아 add 해도 채운 칸이 남는다."""
    m = intake_mod
    _put(m.INBOX / "none_a.jpg", b"a")
    assert m.cmd_add(Namespace()) == 0
    rows = _read_csv(m.LABELS_CSV)
    rows[0].update(category="electronics", photo_type="product", key_texts="SONY")
    with open(m.LABELS_CSV, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=m.LABEL_COLS)
        w.writeheader()
        w.writerows(rows)
    _put(m.INBOX / "none_b.jpg", b"b")

    assert m.cmd_add(Namespace()) == 0
    rows = {r["file"]: r for r in _read_csv(m.LABELS_CSV)}
    assert rows["none_a.jpg"]["photo_type"] == "product" and rows["none_a.jpg"]["key_texts"] == "SONY"
    assert rows["none_b.jpg"]["photo_type"] == ""
    assert [ln for ln in m.SPLIT_LOG.read_text(encoding="utf-8").splitlines() if ln]   # split 기록


def test_add_refuses_over_quota_and_unsupported_format(intake_mod, capsys):
    m = intake_mod
    m.save_dataset([_entry(f"inside_{i}.jpg", stratum="inside") for i in range(10)])   # inside 목표 8+2
    _put(m.INBOX / "inside_more.jpg", b"m")
    assert m.cmd_add(Namespace()) == 1
    assert "inside 층 목표(10개)를 넘는다" in capsys.readouterr().out
    (m.INBOX / "inside_more.jpg").unlink()
    _put(m.INBOX / "none_ok.jpg", b"ok")
    _put(m.INBOX / "none_phone.heic", b"h")
    assert m.cmd_add(Namespace()) == 1
    assert "none_phone.heic" in capsys.readouterr().out and (m.INBOX / "none_ok.jpg").exists()


def test_read_urls_last_field_is_url_and_accepts_bom_tabs_spaces(intake_mod, tmp_path):
    p = tmp_path / "urls.txt"
    p.write_text("\ufeffnone_a.jpg https://x/1\nnone_air pods.jpg\thttps://x/2\nnone_c.jpg\n",
                 encoding="utf-8")
    urls, warnings = intake_mod.read_urls(p)
    assert urls == {"none_a.jpg": "https://x/1", "none_air pods.jpg": "https://x/2"}
    assert len(warnings) == 1 and "3번째 줄" in warnings[0]


def test_merge_handles_excel_quirks(intake_mod, tmp_path):
    """cp949 로 다시 저장 · 빈 줄 · 대소문자 · 날짜로 바뀐 글자(경고)."""
    m = intake_mod
    entries = [_entry("none_a.jpg")]
    p = tmp_path / "excel.csv"
    p.write_text("file,category,photo_type,wear_level,text_level,key_texts,item\n"
                 "none_a.jpg,Bag,Product,None,SIMPLE,2024-01-02|가방,백\n,,,,,,\n", encoding="cp949")
    rows = m.read_csv_rows(p)
    n, errors, warnings = m.merge_labels(entries, rows, "x")
    assert (n, errors) == (1, [])
    assert entries[0]["photo_type"] == "product" and entries[0]["item"] == "백"
    assert len(warnings) == 1 and "2024-01-02" in warnings[0]


def test_merge_rejects_duplicate_rows_and_changing_human_test_label(intake_mod):
    m = intake_mod
    # 새 칸까지 다 있는 사람 라벨 — 없으면 '처음 채움'으로 통과해 동결이 photo_type 하나로만 검증된다
    done = _entry("none_t.jpg", split="test", labeled=True, labeled_by="철수",
                  category="shoes", item_count=1, edge_tags=[])
    entries = [done, _entry("none_a.jpg")]
    row = {"file": "none_a.jpg", "category": "shoes", "photo_type": "product", "wear_level": "none", "text_level": "none"}
    _, errors, _ = m.merge_labels(entries, [row, dict(row)], "x")
    assert errors == ["none_a.jpg: 두 줄 있다"]
    same = {"file": "none_t.jpg", "category": "shoes", "photo_type": "product", "wear_level": "none", "text_level": "none"}
    assert m.merge_labels(entries, [same], "영희")[:2] == (1, [])          # 같은 값은 통과
    done["labeled_by"] = "철수"
    change = dict(same, photo_type="document")
    _, errors, _ = m.merge_labels(entries, [change], "영희")
    assert errors == ["none_t.jpg: test 사진의 라벨(철수)을 바꾸려 한다 — 정말 바꿀 거면 --relabel"]
    assert done["photo_type"] == "product"
    assert m.merge_labels(entries, [change], "영희", relabel=True)[0] == 1
    assert done["photo_type"] == "document"


def test_draft_labels_are_exported_for_human_check(intake_mod):
    m = intake_mod
    entries = [_entry("book.webp", stratum="", split="dev", labeled=True, labeled_by="claude-draft")]
    assert m.write_labels_csv(entries, m.LABELS_CSV) == 1
    assert _read_csv(m.LABELS_CSV)[0]["photo_type"] == "product"   # 초안 값을 보여 주고 확인받는다


def test_run_label_values_match_intake_enums(intake_mod, run_mod):
    assert run_mod.LABEL_VALUES == intake_mod.ENUMS


def test_add_pilot_goes_to_dev_outside_quota(intake_mod):
    """파일럿은 test 자리를 쓰지 않는다 — 층이 다 찼어도 들어가고, 나중 본 수집의 split 에 영향 없음."""
    m = intake_mod
    m.save_dataset([_entry(f"inside_{i}.jpg", stratum="inside") for i in range(10)])   # inside 가득
    _put(m.INBOX / "inside_p.jpg", b"p")
    _put(m.INBOX / "none_q.jpg", b"q")
    assert m.cmd_add(Namespace(pilot=True)) == 0
    e = {x["file"]: x for x in m.load_dataset()}
    assert e["inside_p.jpg"]["split"] == "dev" and e["inside_p.jpg"]["stratum"] == ""
    assert e["inside_p.jpg"]["legacy_stratum"] == "inside" and e["inside_p.jpg"]["pilot"] is True
    _put(m.INBOX / "none_r.jpg", b"r")
    assert m.cmd_add(Namespace()) == 0
    assert m.load_dataset()[-1]["split"] == m.split_slots("none")[0]   # none 층의 첫 자리 그대로


# ── save_dataset 원자성 · dataset_lock ──
def test_save_dataset_leaves_no_temp_file(intake_mod, tmp_path):
    m = intake_mod
    m.save_dataset([_entry("none_a.jpg")])
    m.save_dataset([_entry("none_b.jpg")])
    assert [e["file"] for e in m.load_dataset()] == ["none_b.jpg"]
    assert not list(tmp_path.glob(".dataset-*.tmp")) and not list(tmp_path.glob("*.json.tmp"))


def test_save_dataset_failure_keeps_old_file_and_removes_temp(intake_mod, tmp_path, monkeypatch):
    import pytest
    m = intake_mod
    m.save_dataset([_entry("none_a.jpg")])
    before = m.DATASET.read_text(encoding="utf-8")

    def boom(*a):
        raise OSError("disk full")
    monkeypatch.setattr(m.os, "replace", boom)
    with pytest.raises(OSError):
        m.save_dataset([_entry("none_b.jpg")])
    assert m.DATASET.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob(".dataset-*.tmp"))


def test_save_dataset_failure_on_unserializable_does_not_touch_file(intake_mod, tmp_path):
    import pytest
    m = intake_mod
    m.save_dataset([_entry("none_a.jpg")])
    before = m.DATASET.read_text(encoding="utf-8")
    with pytest.raises(TypeError):
        m.save_dataset([{"file": object()}])
    assert m.DATASET.read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob(".dataset-*.tmp"))


def test_dataset_lock_is_exclusive_and_reusable(intake_mod):
    import threading
    import time
    m = intake_mod
    order = []
    entered = threading.Event()

    def holder():
        with m.dataset_lock():
            order.append("A in")
            entered.set()
            time.sleep(0.2)
            order.append("A out")

    t = threading.Thread(target=holder)
    t.start()
    entered.wait(5)
    with m.dataset_lock():                 # 다른 열린 파일 → flock 이 A 가 놓을 때까지 기다린다
        order.append("B in")
    t.join()
    assert order == ["A in", "A out", "B in"]
    assert m.DATASET.with_suffix(".json.lock").exists()
    with m.dataset_lock():                 # 놓은 뒤엔 다시 잡힌다
        pass


def test_dataset_lock_released_on_exception(intake_mod):
    import pytest
    m = intake_mod
    with pytest.raises(RuntimeError):
        with m.dataset_lock():
            raise RuntimeError
    with m.dataset_lock():
        pass


def _record_lock(m, monkeypatch):
    import contextlib
    state = {"held": False, "seen": []}
    real = m.dataset_lock

    @contextlib.contextmanager
    def lock():
        with real():
            state["held"] = True
            try:
                yield
            finally:
                state["held"] = False
    monkeypatch.setattr(m, "dataset_lock", lock)
    return state


def test_main_runs_add_and_merge_inside_lock_but_not_status_export(intake_mod, monkeypatch, capsys):
    m = intake_mod
    state = _record_lock(m, monkeypatch)
    for name in ("cmd_add", "cmd_merge", "cmd_status", "cmd_export"):
        monkeypatch.setattr(m, name, lambda a, n=name: state["seen"].append((n, state["held"])) or 7)
    assert m.main(["add"]) == 7
    assert m.main(["merge", "x.csv", "--by", "영희"]) == 7
    assert m.main(["status"]) == 7
    assert m.main(["export"]) == 7
    assert state["seen"] == [("cmd_add", True), ("cmd_merge", True), ("cmd_status", False),
                             ("cmd_export", False)]
    assert state["held"] is False


# ── category · item_count · edge_tags (10-04) ──
import pytest  # noqa: E402

_OK = {"category": "bag", "photo_type": "product", "wear_level": "none", "text_level": "none"}


def _row(file="none_a.jpg", **kw):
    return {"file": file, **_OK, **kw}


def _write_csv(path, rows, cols):
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def test_label_cols_have_new_columns_and_categories(intake_mod):
    m = intake_mod
    for c in ("category", "item_count", "edge_tags"):
        assert c in m.LABEL_COLS and c in m.FILLED_COLS
    assert "file" not in m.FILLED_COLS and "stratum" not in m.FILLED_COLS
    assert set(m.FILLED_COLS) < set(m.LABEL_COLS)
    assert m.DATA_CATEGORIES[-2:] == ("watch", "media")
    assert "other" in m.DATA_CATEGORIES and len(set(m.DATA_CATEGORIES)) == len(m.DATA_CATEGORIES)
    assert set(m.EDGE_TAGS.values()) == {"judge", "cut", "gen", "privacy"}
    assert set(m.TAG_MIN) <= set(m.EDGE_TAGS) | {"multi"}           # 최소 수가 붙은 이름은 모두 표에 나온다


def test_round_trip_export_csv_merge_keeps_tags_count_category(intake_mod):
    """export → CSV 에 채움 → merge → export --all → merge: 값이 그대로, 타입도 그대로."""
    m = intake_mod
    m.save_dataset([_entry("none_a.jpg"), _entry("edge_b.jpg", stratum="edge", split="dev")])
    assert m.main(["export"]) == 0
    rows = _read_csv(m.LABELS_CSV)
    assert list(rows[0]) == m.LABEL_COLS
    assert rows[0]["category"] == rows[0]["item_count"] == rows[0]["edge_tags"] == ""
    rows[0].update(_OK, category="Watch", item_count="3", edge_tags="glossy_material|hand_held")
    rows[1].update(_OK, category="media", edge_tags="")             # item_count 빈칸 → 1
    _write_csv(m.LABELS_CSV, rows, m.LABEL_COLS)
    assert m.main(["merge", str(m.LABELS_CSV), "--by", "영희"]) == 0

    a, b = m.load_dataset()
    assert (a["category"], a["item_count"], a["edge_tags"]) == ("watch", 3, ["glossy_material", "hand_held"])
    assert (b["category"], b["item_count"], b["edge_tags"]) == ("media", 1, [])
    assert type(a["item_count"]) is int and type(b["item_count"]) is int

    # 다시 내보내 그대로 merge 해도 (none_a 는 test · 사람 라벨 → 동결 검사 통과해야) 아무것도 안 바뀐다.
    # labels.csv 가 남아 있으면 그 칸이 dataset 값보다 우선한다 (kept) — 'Watch' 가 그대로 남는다
    before = m.load_dataset()
    assert m.main(["export", "--all"]) == 0
    rows = _read_csv(m.LABELS_CSV)
    assert (rows[0]["category"], rows[0]["item_count"], rows[0]["edge_tags"]) == \
        ("Watch", "3", "glossy_material|hand_held")
    assert (rows[1]["item_count"], rows[1]["edge_tags"]) == ("", "")         # 채운 줄이라 CSV 의 빈칸 유지
    assert m.main(["merge", str(m.LABELS_CSV), "--by", "영희"]) == 0
    assert m.load_dataset() == before
    # CSV 를 지우고 내보내면 dataset 값 그대로 (소문자 · 정수 → 글자 · 목록 → | )
    m.LABELS_CSV.unlink()
    assert m.main(["export", "--all"]) == 0
    rows = _read_csv(m.LABELS_CSV)
    assert (rows[0]["category"], rows[0]["item_count"], rows[0]["edge_tags"]) == \
        ("watch", "3", "glossy_material|hand_held")
    assert (rows[1]["category"], rows[1]["item_count"], rows[1]["edge_tags"]) == ("media", "1", "")
    assert m.main(["merge", str(m.LABELS_CSV), "--by", "영희"]) == 0
    assert m.load_dataset() == before


def test_write_labels_csv_keeps_filled_new_columns_from_existing_csv(intake_mod):
    """반쯤 채운 CSV 가 있을 때 다시 쓰면 category · item_count · edge_tags 칸도 살아남는다."""
    m = intake_mod
    entries = [_entry("none_a.jpg"), _entry("none_b.jpg")]
    m.write_labels_csv(entries, m.LABELS_CSV)
    rows = _read_csv(m.LABELS_CSV)
    rows[0].update(category="shoes", item_count="2", edge_tags="prop|tag")   # 이것만 채워도 '채운 줄'
    _write_csv(m.LABELS_CSV, rows, m.LABEL_COLS)

    entries.append(_entry("none_c.jpg"))
    assert m.write_labels_csv(entries, m.LABELS_CSV) == 3
    rows = {r["file"]: r for r in _read_csv(m.LABELS_CSV)}
    assert (rows["none_a.jpg"]["category"], rows["none_a.jpg"]["item_count"],
            rows["none_a.jpg"]["edge_tags"]) == ("shoes", "2", "prop|tag")
    assert rows["none_c.jpg"]["edge_tags"] == ""
    # 남은 칸을 채워 merge 하면 정수 · 목록으로 들어간다
    rows["none_a.jpg"].update(photo_type="product", wear_level="light", text_level="none")
    n, errors, _ = m.merge_labels(entries, list(rows.values()), "x")
    assert (n, errors) == (1, [])
    assert (entries[0]["item_count"], entries[0]["edge_tags"]) == (2, ["prop", "tag"])


def test_write_labels_csv_old_header_csv_keeps_dataset_values_for_new_columns(intake_mod):
    """새 열이 없는 옛 CSV 를 살릴 때 — 옛 CSV 에 없는 열은 dataset(초안) 값을 지우지 않는다."""
    m = intake_mod
    draft = _entry("none_a.jpg", labeled=True, labeled_by=m.DRAFT, category="bag", item_count=2,
                   edge_tags=["prop"])
    old_cols = ["file", "stratum", "item", "photo_type", "wear_level", "text_level", "key_texts",
                "ambiguous", "note"]
    _write_csv(m.LABELS_CSV, [{"file": "none_a.jpg", "photo_type": "document", "note": "옛"}], old_cols)
    assert m.write_labels_csv([draft], m.LABELS_CSV) == 1
    r = _read_csv(m.LABELS_CSV)[0]
    assert (r["photo_type"], r["note"]) == ("document", "옛")                    # 옛 CSV 칸은 살림
    assert (r["category"], r["item_count"], r["edge_tags"]) == ("bag", "2", "prop")   # 없는 열은 초안 값


def test_row_of_writes_draft_values_and_handles_old_entries(intake_mod):
    m = intake_mod
    r = m._row_of(_entry("x.jpg", category="bag", item_count=2, edge_tags=["prop", "tag"]))
    assert (r["category"], r["item_count"], r["edge_tags"]) == ("bag", 2, "prop|tag")
    old = m._row_of({"file": "old.jpg"})                           # 새 열이 없는 옛 항목
    assert (old["category"], old["item_count"], old["edge_tags"]) == ("", "", "")
    assert m._row_of(_entry("y.jpg", edge_tags=None, item_count=None))["edge_tags"] == ""
    assert set(r) == set(m.LABEL_COLS)


@pytest.mark.parametrize("raw, want", [
    ("", 1), ("1", 1), (" 4 ", 4), ("01", 1), ("10", 10)])
def test_item_count_valid(intake_mod, raw, want):
    vals, errors, _ = intake_mod.parse_label_row(_row(item_count=raw))
    assert errors == [] and vals["item_count"] == want and type(vals["item_count"]) is int


@pytest.mark.parametrize("raw", ["0", "00", "-1", "2개", "2.0", "+2", "two", "1 2", "1e1",
                                 "²", "２", "٢"])        # 유니코드 숫자: isdigit() 는 True 지만 받지 않는다
def test_item_count_invalid(intake_mod, raw):
    vals, errors, _ = intake_mod.parse_label_row(_row(item_count=raw))
    assert vals is None
    assert errors == [f"item_count={raw.strip()!r} (1 이상 정수, 빈칸이면 1 — 팔 물건 개수)"]


@pytest.mark.parametrize("raw", ["Bag", " BAG ", "watch", "Media", "other"])
def test_category_case_insensitive(intake_mod, raw):
    vals, errors, _ = intake_mod.parse_label_row(_row(category=raw))
    assert errors == [] and vals["category"] == raw.strip().lower()


@pytest.mark.parametrize("raw", ["", "  ", "가방", "bags", "toy"])
def test_category_missing_or_unknown_is_error(intake_mod, raw):
    m = intake_mod
    vals, errors, _ = m.parse_label_row(_row(category=raw))
    assert vals is None and len(errors) == 1
    assert errors[0].startswith(f"category={raw.strip()!r} (가능: ")
    assert "/".join(m.DATA_CATEGORIES) in errors[0]


def test_category_column_absent_is_error(intake_mod):
    """category 열이 아예 없는 옛 CSV — 빈칸과 같이 오류."""
    row = _row()
    del row["category"]
    vals, errors, _ = intake_mod.parse_label_row(row)
    assert vals is None and errors[0].startswith("category=''")


def test_edge_tags_case_space_duplicates_order(intake_mod):
    vals, errors, _ = intake_mod.parse_label_row(
        _row(edge_tags=" Hand_Held | PROP|| hand_held |prop | | Tag "))
    assert errors == []
    assert vals["edge_tags"] == ["hand_held", "prop", "tag"]       # 처음 나온 순서 · 중복 제거


@pytest.mark.parametrize("raw", ["", "   ", "|", " | | "])
def test_edge_tags_blank_is_empty_list(intake_mod, raw):
    vals, errors, _ = intake_mod.parse_label_row(_row(edge_tags=raw))
    assert errors == [] and vals["edge_tags"] == []


def test_edge_tags_unknown_lists_each_once_lowercased(intake_mod):
    vals, errors, _ = intake_mod.parse_label_row(
        _row(edge_tags="prop|Blurry|blurry|hand held|prop,tag"))
    assert vals is None and len(errors) == 1
    assert errors[0].startswith("edge_tags blurry, hand held, prop,tag — 없는 태그 (구분자는 `|`.")
    assert "EDGE_TAGS" in errors[0]


def test_every_edge_tag_is_accepted(intake_mod):
    m = intake_mod
    vals, errors, _ = m.parse_label_row(_row(edge_tags="|".join(m.EDGE_TAGS).upper()))
    assert errors == [] and vals["edge_tags"] == list(m.EDGE_TAGS)


def test_only_new_columns_filled_without_enums_is_error(intake_mod):
    """category · edge_tags · item_count 만 채운 줄은 '안 단 줄' 이 아니라 오류."""
    for kw in ({"category": "bag"}, {"edge_tags": "prop"}, {"item_count": "2"}):
        vals, errors, _ = intake_mod.parse_label_row({"file": "a.jpg", **kw})
        assert vals is None and errors == ["photo_type · wear_level · text_level 이 비었는데 다른 칸만 채웠다"]


def test_all_new_errors_reported_together(intake_mod):
    vals, errors, _ = intake_mod.parse_label_row(
        _row(category="", item_count="0", edge_tags="nope"))
    assert vals is None and len(errors) == 3
    assert [e.split("=")[0].split(" ")[0] for e in errors] == ["category", "item_count", "edge_tags"]


def test_merge_with_any_bad_new_value_changes_nothing(intake_mod, tmp_path, capsys):
    m = intake_mod
    m.save_dataset([_entry("none_a.jpg"), _entry("none_b.jpg"), _entry("none_c.jpg"),
                    _entry("none_d.jpg"), _entry("none_e.jpg")])
    before = m.DATASET.read_text(encoding="utf-8")
    p = tmp_path / "bad.csv"
    _write_csv(p, [_row("none_a.jpg", item_count="2", edge_tags="prop"),   # 좋은 줄
                   _row("none_b.jpg", edge_tags="sparkly"),
                   _row("none_c.jpg", item_count="2개"),
                   _row("none_d.jpg", item_count="-1"),
                   _row("none_e.jpg", category="")], m.LABEL_COLS)
    assert m.main(["merge", str(p), "--by", "영희"]) == 1
    out = capsys.readouterr().out
    assert "반영하지 않았다" in out
    assert "none_b.jpg: edge_tags sparkly — 없는 태그" in out
    assert "none_c.jpg: item_count='2개'" in out
    assert "none_d.jpg: item_count='-1'" in out
    assert "none_e.jpg: category=''" in out
    assert "none_a.jpg" not in out
    assert m.DATASET.read_text(encoding="utf-8") == before        # 좋은 줄도 반영 안 됨


# ── test 라벨 동결 ──
def _human_test(**kw):
    base = dict(split="test", labeled=True, labeled_by="철수", category="bag", item_count=1,
                edge_tags=[])
    base.update(kw)
    return _entry("none_t.jpg", **base)


@pytest.mark.parametrize("change", [
    {"category": "shoes"}, {"item_count": "2"}, {"edge_tags": "prop"}, {"ambiguous": "y"}])
def test_test_label_freeze_blocks_new_frozen_fields(intake_mod, change):
    m = intake_mod
    done = _human_test()
    snap = dict(done)
    n, errors, _ = m.merge_labels([done], [_row("none_t.jpg", **change)], "영희")
    assert n == 0 and "--relabel" in errors[0] and "철수" in errors[0]
    assert done == snap
    assert m.merge_labels([done], [_row("none_t.jpg", **change)], "영희", relabel=True)[0] == 1
    assert done["category"] == change.get("category", "bag")
    assert done["item_count"] == int(change.get("item_count", 1))


def test_test_label_freeze_same_values_in_other_spelling_pass(intake_mod):
    """대소문자 · 순서 같은 태그 · 빈칸 item_count(=1) 은 바꾸는 게 아니다. note · item 은 동결 밖."""
    m = intake_mod
    done = _human_test(edge_tags=["prop", "tag"])
    row = _row("none_t.jpg", category=" BAG ", item_count="", edge_tags="PROP| tag |prop",
               note="메모", item="가방")
    assert m.merge_labels([done], [row], "영희")[:2] == (1, [])
    assert (done["note"], done["item"], done["labeled_by"]) == ("메모", "가방", "영희")


def test_test_label_freeze_ignores_edge_tag_order(intake_mod):
    """태그 순서만 다르면 바꾼 게 아니다 — 태그가 더해지거나 빠지면 동결."""
    m = intake_mod
    done = _human_test(edge_tags=["prop", "tag"])
    assert m.merge_labels([done], [_row("none_t.jpg", edge_tags="tag|prop")], "x")[:2] == (1, [])
    assert "--relabel" in m.merge_labels([done], [_row("none_t.jpg", edge_tags="tag")], "x")[1][0]


def test_test_label_freeze_allows_filling_blank_values(intake_mod):
    """손으로 고친 JSON 에 빈 문자열로 남은 칸은 처음 채우는 것 — 막지 않는다. 빈 태그 목록 [] 은 라벨이라 동결."""
    m = intake_mod
    old = _entry("none_t.jpg", split="test", labeled=True, labeled_by="철수", category="", edge_tags=[])
    assert m.merge_labels([old], [_row("none_t.jpg", category="shoes")], "영희")[:2] == (1, [])
    assert "--relabel" in m.merge_labels([old], [_row("none_t.jpg", category="shoes",
                                                      edge_tags="hand_held")], "x")[1][0]


def test_test_label_freeze_allows_first_fill_on_old_entries(intake_mod):
    """새 칸이 생기기 전에 사람이 단 test 사진 — 없던 칸을 처음 채우는 건 막지 않는다."""
    m = intake_mod
    old = _entry("none_t.jpg", split="test", labeled=True, labeled_by="철수")   # category · item_count · edge_tags 없음
    row = _row("none_t.jpg", category="shoes", item_count="2", edge_tags="hand_held")
    assert m.merge_labels([old], [row], "영희")[:2] == (1, [])
    assert (old["category"], old["item_count"], old["edge_tags"]) == ("shoes", 2, ["hand_held"])
    # 한 번 채운 뒤엔 동결
    assert "--relabel" in m.merge_labels([old], [_row("none_t.jpg", category="bag", item_count="2",
                                                      edge_tags="hand_held")], "x")[1][0]
    # 바뀌는 칸이 기존 칸(photo_type)이면 첫 채움과 함께여도 막는다
    old2 = _entry("none_u.jpg", split="test", labeled=True, labeled_by="철수")
    assert m.merge_labels([old2], [_row("none_u.jpg", photo_type="document")], "x")[0] == 0


def test_freeze_not_applied_to_dev_or_draft(intake_mod):
    m = intake_mod
    dev = _entry("none_d.jpg", split="dev", labeled=True, labeled_by="철수", category="bag", item_count=1)
    draft = _entry("none_t.jpg", split="test", labeled=True, labeled_by=m.DRAFT,
                   category="bag", item_count=1, edge_tags=[])
    rows = [_row("none_d.jpg", category="shoes", item_count="3"),
            _row("none_t.jpg", category="shoes", item_count="3", edge_tags="prop")]
    assert m.merge_labels([dev, draft], rows, "영희")[:2] == (2, [])
    assert dev["item_count"] == draft["item_count"] == 3 and draft["edge_tags"] == ["prop"]


# ── tag_table · status ──
def _tag_line(table, name):
    return next(ln for ln in table.splitlines() if ln.startswith(f"| {name} |"))


def _cells(line):
    return [c.strip() for c in line.strip("|").split("|")]


def test_tag_table_header_and_rows(intake_mod):
    m = intake_mod
    lines = m.tag_table([]).splitlines()
    assert lines[0] == "| 태그 | 묶음 | 규칙대로 | 보충 | 최소 | 파일럿·기존 |"
    assert len(lines) == 2 + len(m.EDGE_TAGS) + 1
    assert [_cells(ln)[0] for ln in lines[2:-1]] == list(m.EDGE_TAGS)
    assert lines[-1] == f"| multi (item_count ≥ 2) | cut | 0 | 0 | {m.TAG_MIN['multi']} ⚠ | 0 |"
    for ln in lines[2:]:
        name, _, n, s, need, rest = _cells(ln)
        key = "multi" if name.startswith("multi") else name
        assert (n, s, rest) == ("0", "0", "0")
        assert need == (f"{m.TAG_MIN[key]} ⚠" if key in m.TAG_MIN else "")   # 최소 없는 태그는 ⚠ 없음


def test_tag_table_threshold_counts_ruled_plus_supplement_not_pilot(intake_mod):
    m = intake_mod
    assert m.TAG_MIN["transparent"] == 5
    ruled = [_entry(f"none_{i}.jpg", edge_tags=["transparent", "pattern"] if i < 3 else ["pattern"])
             for i in range(5)]
    supp = [_entry(f"s{i}.jpg", stratum="", split="dev", supplement=True, legacy_stratum="none",
                   edge_tags=["transparent"]) for i in range(2)]
    pilot = [_entry(f"p{i}.jpg", stratum="", split="dev", pilot=True, legacy_stratum="none",
                    edge_tags=["glossy_material", "private"]) for i in range(9)]
    legacy = [{"file": "old.jpg", "edge_tags": ["glossy_material"]}]   # stratum 키도 없는 옛 항목
    table = m.tag_table(ruled + supp + pilot + legacy)
    assert _tag_line(table, "transparent") == "| transparent | cut | 3 | 2 | 5 | 0 |"      # 3+2 = 딱 최소
    assert _tag_line(table, "pattern") == "| pattern | gen | 5 | 0 | 5 | 0 |"
    assert _tag_line(table, "glossy_material") == "| glossy_material | cut | 0 | 0 | 5 ⚠ | 10 |"  # 파일럿은 안 셈
    assert _tag_line(table, "private") == "| private | privacy | 0 | 0 |  | 9 |"          # 최소 없음

    supp.pop()
    assert _tag_line(m.tag_table(ruled + supp), "transparent") == "| transparent | cut | 3 | 1 | 5 ⚠ | 0 |"


def test_tag_table_unknown_stratum_and_supplement_with_stratum(intake_mod):
    m = intake_mod
    es = [_entry(f"x{i}.jpg", stratum="cat", edge_tags=["pattern"]) for i in range(6)]    # 모르는 층 → 기존
    es.append(_entry("none_s.jpg", supplement=True, edge_tags=["pattern"]))              # 층이 있으면 규칙대로
    assert _tag_line(m.tag_table(es), "pattern") == "| pattern | gen | 1 | 0 | 5 ⚠ | 6 |"


def test_tag_table_multi_row_and_odd_item_counts(intake_mod):
    m = intake_mod
    ruled = [_entry(f"none_{i}.jpg", item_count=2 + i) for i in range(3)]
    ruled += [_entry("none_one.jpg", item_count=1), _entry("none_old.jpg"),     # 옛 항목: item_count 없음 → 1
              _entry("none_none.jpg", item_count=None, edge_tags=None),
              _entry("none_str.jpg", item_count="2"),                           # 손으로 고친 JSON
              _entry("none_bad.jpg", item_count="2개"), _entry("none_zero.jpg", item_count=0)]
    supp = [_entry("s.jpg", stratum="", supplement=True, item_count=4)]
    rest = [_entry("p.jpg", stratum="", pilot=True, item_count=3), {"file": "legacy.jpg"}]
    multi = m.tag_table(ruled + supp + rest).splitlines()[-1]
    assert multi == "| multi (item_count ≥ 2) | cut | 4 | 1 | 5 | 1 |"
    assert m.tag_table(ruled).splitlines()[-1] == "| multi (item_count ≥ 2) | cut | 4 | 0 | 5 ⚠ | 0 |"


def test_tag_table_string_edge_tags_is_one_tag(intake_mod):
    """edge_tags 가 문자열이면 글자 단위가 아니라 통째 한 태그 ("tag" 가 "packaging" 안에서 잡히면 안 된다)."""
    m = intake_mod
    table = m.tag_table([_entry("none_a.jpg", edge_tags="packaging")])
    assert _tag_line(table, "packaging") == "| packaging | gen | 1 | 0 | 5 ⚠ | 0 |"
    assert _tag_line(table, "tag") == "| tag | gen | 0 | 0 |  | 0 |"


def test_cmd_status_prints_tag_table_and_unknown_tags(intake_mod, capsys):
    m = intake_mod
    m.save_dataset([_entry("none_a.jpg", edge_tags=["pattern", "zz_old", "blurry"]),
                    _entry("p.jpg", stratum="", edge_tags=["blurry"], item_count=2)])
    assert m.cmd_status(Namespace()) == 0
    out = capsys.readouterr().out
    assert "| 태그 | 묶음 | 규칙대로 | 보충 | 최소 | 파일럿·기존 |" in out
    assert "| pattern | gen | 1 | 0 | 5 ⚠ | 0 |" in out
    assert "| multi (item_count ≥ 2) | cut | 0 | 0 | 5 ⚠ | 1 |" in out
    assert "EDGE_TAGS 에 없는 태그: blurry, zz_old" in out           # 정렬 · 한 번씩


def test_cmd_status_no_unknown_tag_line_when_all_known(intake_mod, capsys):
    m = intake_mod
    m.save_dataset([_entry("none_a.jpg", edge_tags=["prop"]), {"file": "old.jpg"}])
    assert m.cmd_status(Namespace()) == 0
    assert "EDGE_TAGS 에 없는 태그" not in capsys.readouterr().out


def test_cmd_status_string_edge_tags_reported_whole(intake_mod, capsys):
    m = intake_mod
    m.save_dataset([_entry("none_a.jpg", edge_tags="blurry")])
    assert m.cmd_status(Namespace()) == 0
    assert "EDGE_TAGS 에 없는 태그: blurry" in capsys.readouterr().out


# ── --supplement ──
def test_add_supplement_goes_to_dev_outside_quota(intake_mod, capsys):
    m = intake_mod
    m.save_dataset([_entry(f"inside_{i}.jpg", stratum="inside") for i in range(10)])   # inside 가득
    _put(m.INBOX / "inside_s.jpg", b"s")
    assert m.cmd_add(Namespace(supplement=True)) == 0
    assert "1장 추가 (보충 — 층 목표 밖)" in capsys.readouterr().out
    e = m.load_dataset()[-1]
    assert (e["stratum"], e["legacy_stratum"], e["split"], e["supplement"]) == ("", "inside", "dev", True)
    assert "pilot" not in e
    assert json.loads(m.SPLIT_LOG.read_text(encoding="utf-8").strip()) == {
        "file": "inside_s.jpg", "stratum": "inside", "split": "dev", "post": "inside_s", "at": e["collected_at"]}
    _put(m.INBOX / "none_r.jpg", b"r")                       # 본 수집은 첫 자리부터
    assert m.cmd_add(Namespace()) == 0
    assert m.load_dataset()[-1]["split"] == m.split_slots("none")[0]


def test_add_pilot_and_supplement_together_refused(intake_mod, capsys):
    m = intake_mod
    _put(m.INBOX / "none_a.jpg", b"a")
    assert m.cmd_add(Namespace(pilot=True, supplement=True)) == 1
    assert "같이 못 쓴다" in capsys.readouterr().out
    assert (m.INBOX / "none_a.jpg").exists() and m.load_dataset() == []


def test_main_add_supplement_flag(intake_mod, monkeypatch):
    m = intake_mod
    seen = []
    monkeypatch.setattr(m, "cmd_add", lambda a: seen.append((a.pilot, a.supplement)) or 0)
    assert m.main(["add", "--supplement"]) == 0 and m.main(["add"]) == 0
    assert seen == [(False, True), (False, False)]


# ── 게시글 단위 (10-04) — 폴더 = 게시글, 첫 사진만 층 · split 을 갖고 나머지는 따른다 ──
def _post(m, name, photos):
    """inbox 에 게시글 폴더 — photos: {파일 이름: 바이트}."""
    d = m.INBOX / name
    d.mkdir()
    for n, data in photos.items():
        (d / n).write_bytes(data)
    return d


def _n(k, tag="p"):
    """사진 k 장 {1.jpg: …, 2.jpg: …} — 내용은 서로 다르게."""
    return {f"{i}.jpg": f"{tag}{i}".encode() for i in range(1, k + 1)}


def _by_file(entries):
    return {e["file"]: e for e in entries}


def _tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


def test_strata_wear_replaces_light_heavy_and_post_max(intake_mod):
    m = intake_mod
    assert "light" not in m.STRATA and "heavy" not in m.STRATA
    assert m.STRATA["wear"] == (35, 8) and m.POST_MAX == 10
    assert m.stratum_of("wear_x") == "wear" and m.stratum_of("light_x") is None
    assert m.ENUMS["wear_level"] == ("none", "light", "heavy")      # 라벨 값은 그대로


# ── inbox_files ──
def test_inbox_files_folders_single_files_and_others(intake_mod):
    m = intake_mod
    _post(m, "none_a", {"1.jpg": b"a1", "notes.txt": b"n", ".DS_Store": b"x", "2.JPG": b"a2"})
    (m.INBOX / "none_a" / "sub").mkdir()
    _post(m, "doc_b", {"1.png": b"b1"})
    _put(m.INBOX / "wear_c.webp", b"c")
    _put(m.INBOX / "readme.md", b"r")
    _put(m.INBOX / ".hidden.jpg", b"h")
    _put(m.INBOX / "urls.txt", b"")
    _put(m.INBOX / "labels.csv", b"")
    (m.INBOX / ".git").mkdir()                                         # 숨김 폴더도 게시글 아님

    posts, other = m.inbox_files(m.INBOX)
    assert [p.name for p in posts] == ["doc_b", "none_a", "wear_c.webp"]
    assert other == ["none_a/notes.txt", "none_a/sub", "readme.md"]    # 숨김 파일은 폴더 안에서도 빠진다


def test_inbox_files_missing_inbox(intake_mod, tmp_path):
    assert intake_mod.inbox_files(tmp_path / "nope") == ([], [])


# ── post_photos ──
def test_post_photos_numeric_not_lexical_order_and_filters(intake_mod):
    m = intake_mod
    d = _post(m, "none_a", {"10.jpg": b"10", "2.jpg": b"2", "1.JPG": b"1", "3.png": b"3", "4.WEBP": b"4",
                            "5.jpeg": b"5", "6.jpg": b"6", "7.jpg": b"7", "8.jpg": b"8", "9.jpg": b"9",
                            "11.jpg": b"11", ".12.jpg": b"h", "notes.txt": b"t", "13.heic": b"x"})
    (d / "12.jpg").mkdir()                                             # 이름만 사진인 폴더는 안 센다
    photos, errors = m.post_photos(d)
    assert errors == []
    assert [p.name for p in photos] == ["1.JPG", "2.jpg", "3.png", "4.WEBP", "5.jpeg", "6.jpg", "7.jpg",
                                        "8.jpg", "9.jpg", "10.jpg", "11.jpg"]


def test_post_photos_leading_zeros_count_as_numbers(intake_mod):
    m = intake_mod
    photos, errors = m.post_photos(_post(m, "none_z", {"01.jpg": b"1", "002.png": b"2", "3.jpg": b"3"}))
    assert errors == [] and [p.name for p in photos] == ["01.jpg", "002.png", "3.jpg"]


@pytest.mark.parametrize("names, msg", [
    (["1.jpg", "IMG_2.jpg"], "사진 이름은 게시글 순서 번호만 (1.jpg, 2.jpg …) — IMG_2.jpg"),
    (["cover.jpg", "b.png", "1.jpg"], "사진 이름은 게시글 순서 번호만 (1.jpg, 2.jpg …) — b.png, cover.jpg"),
    (["1a.jpg"], "사진 이름은 게시글 순서 번호만 (1.jpg, 2.jpg …) — 1a.jpg"),
    (["1.jpg", "2 .jpg"], "사진 이름은 게시글 순서 번호만 (1.jpg, 2.jpg …) — 2 .jpg"),
    (["１.jpg"], "사진 이름은 게시글 순서 번호만 (1.jpg, 2.jpg …) — １.jpg"),          # 전각 숫자
    (["1.jpg", "01.jpg"], "번호가 1 부터 빠짐 · 겹침 없이 이어져야 한다 — 지금 [1, 1]"),
    (["1.jpg", "1.png"], "번호가 1 부터 빠짐 · 겹침 없이 이어져야 한다 — 지금 [1, 1]"),
    (["1.jpg", "3.jpg"], "번호가 1 부터 빠짐 · 겹침 없이 이어져야 한다 — 지금 [1, 3]"),
    (["2.jpg", "3.jpg"], "번호가 1 부터 빠짐 · 겹침 없이 이어져야 한다 — 지금 [2, 3]"),
    (["0.jpg", "1.jpg"], "번호가 1 부터 빠짐 · 겹침 없이 이어져야 한다 — 지금 [0, 1]"),
])
def test_post_photos_bad_names_are_errors(intake_mod, names, msg):
    m = intake_mod
    d = _post(m, "none_bad", {n: n.encode() for n in names})
    assert m.post_photos(d) == ([], [f"none_bad/: {msg}"])


def test_post_photos_single_file_and_empty_folder(intake_mod):
    m = intake_mod
    f = _put(m.INBOX / "none_x.jpg", b"x")                             # 파일 하나는 이름 규칙 없음
    assert m.post_photos(f) == ([f], [])
    assert m.post_photos(_post(m, "none_e", {"a.txt": b"t", ".DS_Store": b"x"})) == ([], [])


# ── plan_add ──
def test_plan_add_folder_names_fields_and_lowercase_ext(intake_mod):
    m = intake_mod
    d = _post(m, "wear_bag", {"1.JPG": b"1", "2.Png": b"2", "3.jpeg": b"3"})
    single = _put(m.INBOX / "none_one.JPG", b"s")
    urls = {"wear_bag": "https://x/bag", "none_one": "https://x/one"}   # 파일 하나는 확장자 뺀 이름도
    new, errors = m.plan_add([single, d], urls, [], m.IMAGES)
    assert errors == []
    assert [e["file"] for e in new] == ["none_one.JPG",                 # 파일 하나면 이름 그대로 (대문자도)
                                        "wear_bag_p01.jpg", "wear_bag_p02.png", "wear_bag_p03.jpeg"]
    one, p1, p2, p3 = new
    assert (one["post"], one["post_index"], one["post_size"], one["stratum"]) == ("none_one", 1, 1, "none")
    assert one["url"] == "https://x/one" and one["source"] == str(single)
    assert [(e["post"], e["post_index"], e["post_size"]) for e in (p1, p2, p3)] == \
        [("wear_bag", 1, 3), ("wear_bag", 2, 3), ("wear_bag", 3, 3)]
    assert [e["stratum"] for e in (p1, p2, p3)] == ["wear", "", ""]     # 첫 사진만 층
    assert {e["url"] for e in (p1, p2, p3)} == {"https://x/bag"}
    assert [Path(e["source"]).name for e in (p1, p2, p3)] == ["1.JPG", "2.Png", "3.jpeg"]
    for e in new:
        assert "split" not in e and "post_total" not in e and "dup_of" not in e
        assert "legacy_stratum" not in e and "pilot" not in e and "supplement" not in e


def test_plan_add_numeric_order_maps_to_pnn(intake_mod):
    m = intake_mod
    d = _post(m, "none_a", _n(10))
    new, errors = m.plan_add([d], {}, [], m.IMAGES)
    assert errors == []
    assert [(e["file"], Path(e["source"]).name) for e in new][8:] == [("none_a_p09.jpg", "9.jpg"),
                                                                     ("none_a_p10.jpg", "10.jpg")]
    assert all("post_total" not in e for e in new)                      # 딱 10장은 잘리지 않았다


def test_plan_add_single_file_url_by_full_name_wins(intake_mod):
    m = intake_mod
    f = _put(m.INBOX / "none_a.jpg", b"a")
    new, _ = m.plan_add([f], {"none_a.jpg": "https://full", "none_a": "https://stem"}, [], m.IMAGES)
    assert new[0]["url"] == "https://full"


def test_plan_add_over_post_max_keeps_first_ten_with_post_total(intake_mod):
    m = intake_mod
    d = _post(m, "none_big", _n(12))
    new, errors = m.plan_add([d], {}, [], m.IMAGES)
    assert errors == [] and len(new) == m.POST_MAX
    assert [Path(e["source"]).name for e in new] == [f"{i}.jpg" for i in range(1, 11)]   # 앞 10장 (숫자 순)
    assert {(e["post_size"], e["post_total"]) for e in new} == {(10, 12)}
    assert new[-1]["file"] == "none_big_p10.jpg"


def test_plan_add_over_post_max_still_needs_valid_numbering(intake_mod):
    m = intake_mod
    photos = _n(11)
    photos["13.jpg"] = photos.pop("11.jpg")                            # 11 이 빠짐 — 잘릴 뒤쪽이어도 오류
    new, errors = m.plan_add([_post(m, "none_gap", photos)], {}, [], m.IMAGES)
    assert new == [] and errors == [f"none_gap/: 번호가 1 부터 빠짐 · 겹침 없이 이어져야 한다 — 지금 "
                                    f"{list(range(1, 11)) + [13]}"]


def test_plan_add_empty_folder_bad_names_and_bad_stratum(intake_mod):
    m = intake_mod
    empty = _post(m, "none_empty", {".DS_Store": b"x"})
    badname = _post(m, "none_names", {"IMG_1.jpg": b"i"})
    bad = _post(m, "cat_x", {"1.jpg": b"c"})
    new, errors = m.plan_add([empty, badname, bad], {}, [], m.IMAGES)
    assert new == []
    assert sorted(errors) == sorted([
        "none_empty/: 사진이 없다",
        "none_names/: 사진 이름은 게시글 순서 번호만 (1.jpg, 2.jpg …) — IMG_1.jpg",
        f"cat_x: 이름이 <층 코드>_... 가 아니다 (코드: {', '.join(m.STRATA)})"])


def test_plan_add_quota_counts_posts_not_photos(intake_mod):
    """층 목표는 게시글 수 — 이미 있는 추가 사진(stratum "")도, 새 게시글의 나머지 사진도 세지 않는다."""
    m = intake_mod
    entries = [_entry(f"inside_{i}.jpg", stratum="inside", post=f"inside_{i}", post_index=1) for i in range(9)]
    entries += [_entry(f"inside_0_p0{i}.jpg", stratum="", post="inside_0", post_index=i) for i in (2, 3)]
    big = _post(m, "inside_big", _n(5, "b"))
    new, errors = m.plan_add([big], {}, entries, m.IMAGES)
    assert errors == [] and len(new) == 5                               # 10번째 게시글 = 목표 딱
    more = _post(m, "inside_more", {"1.jpg": b"m"})
    _, errors = m.plan_add([big, more], {}, entries, m.IMAGES)
    assert errors == ["inside_more: inside 층 목표(10개)를 넘는다 — 이 층은 다 모았다"]


def test_plan_add_pilot_and_supplement_every_photo_dev_legacy_only_first(intake_mod):
    m = intake_mod
    d = _post(m, "inside_p", _n(3))
    full = [_entry(f"inside_{i}.jpg", stratum="inside") for i in range(10)]   # 층이 가득이어도
    for flag, other in (("pilot", "supplement"), ("supplement", "pilot")):
        new, errors = m.plan_add([d], {}, full, m.IMAGES, **{flag: True})
        assert errors == [] and len(new) == 3
        for e in new:
            assert (e["stratum"], e["split"], e[flag]) == ("", "dev", True) and other not in e
        assert [e.get("legacy_stratum") for e in new] == ["inside", None, None]
        assert [e["post_index"] for e in new] == [1, 2, 3]


def test_plan_add_same_post_name_old_entry_uses_file_stem(intake_mod):
    """옛 항목(post 칸 없음)은 파일 이름에서 확장자를 뺀 것이 게시글 이름."""
    m = intake_mod
    entries = [_entry("none_a.jpg"), _entry("none_b.webp")]
    folder = _post(m, "none_a", {"1.jpg": b"a1"})                     # 파일 이름은 안 겹치지만 게시글 이름이 겹친다
    single = _put(m.INBOX / "none_b.png", b"b")                         # 확장자만 다른 파일
    ok = _put(m.INBOX / "none_c.jpg", b"c")
    new, errors = m.plan_add([folder, single, ok], {}, entries, m.IMAGES)
    assert [e["file"] for e in new] == ["none_c.jpg"]
    assert sorted(errors) == ["none_a: 같은 게시글 이름이 이미 있다", "none_b.png: 같은 게시글 이름이 이미 있다"]


def test_plan_add_same_post_name_new_entry_uses_post(intake_mod):
    m = intake_mod
    entries = [_entry("wear_bag_p01.jpg", stratum="wear", post="wear_bag", post_index=1, post_size=2),
               _entry("wear_bag_p02.jpg", stratum="", post="wear_bag", post_index=2, post_size=2)]
    single = _put(m.INBOX / "wear_bag.jpg", b"s")
    new, errors = m.plan_add([single], {}, entries, m.IMAGES)
    assert new == [] and errors == ["wear_bag.jpg: 같은 게시글 이름이 이미 있다"]
    # post 칸이 있으면 파일 stem 이 아니라 post 로 본다 — wear_bag_p01 이라는 게시글은 없다
    other = _put(m.INBOX / "wear_bag_p01.png", b"o")
    new, errors = m.plan_add([other], {}, entries, m.IMAGES)
    assert errors == [] and new[0]["file"] == "wear_bag_p01.png"


def test_plan_add_same_post_name_folder_and_file_in_one_inbox(intake_mod):
    m = intake_mod
    folder = _post(m, "none_a", {"1.jpg": b"f"})
    single = _put(m.INBOX / "none_a.jpg", b"s")
    new, errors = m.plan_add([single, folder], {}, [], m.IMAGES)
    assert [e["file"] for e in new] == ["none_a_p01.jpg"]                # 정렬 순으로 폴더가 먼저
    assert errors == ["none_a.jpg: 같은 게시글 이름이 이미 있다"]


def test_plan_add_file_name_clash_with_existing_lists_names(intake_mod):
    m = intake_mod
    _put(m.IMAGES / "none_a_p02.jpg", b"disk")                          # dataset 엔 없고 파일만
    entries = [_entry("none_b_p01.png")]
    a = _post(m, "none_a", {"1.jpg": b"1", "2.JPG": b"2"})
    b = _post(m, "none_b", {"1.PNG": b"3"})
    new, errors = m.plan_add([a, b], {}, entries, m.IMAGES)
    assert new == []
    assert sorted(errors) == ["none_a: 같은 이름이 이미 있다 (none_a_p02.jpg)",
                              "none_b: 같은 이름이 이미 있다 (none_b_p01.png)"]


def test_plan_add_generated_name_clash_inside_one_batch(intake_mod):
    """폴더 none_a/1.jpg → none_a_p01.jpg 와 파일 하나 none_a_p01.jpg — 같은 add 안에서도 잡는다."""
    m = intake_mod
    folder = _post(m, "none_a", {"1.jpg": b"folder"})
    single = _put(m.INBOX / "none_a_p01.jpg", b"single")
    new, errors = m.plan_add([single, folder], {}, [], m.IMAGES)
    assert [e["file"] for e in new] == ["none_a_p01.jpg"] and new[0]["post"] == "none_a"
    assert errors == ["none_a_p01.jpg: 같은 이름이 이미 있다"]


def test_plan_add_first_photo_duplicate_is_error(intake_mod):
    m = intake_mod
    _put(m.IMAGES / "none_old.jpg", b"old")
    entries = [_entry("none_old.jpg")]
    a = _post(m, "none_a", {"1.jpg": b"old", "2.jpg": b"a2"})          # 기존과 같은 첫 사진
    b = _post(m, "none_b", {"1.jpg": b"b1", "2.jpg": b"b2"})
    c = _post(m, "none_c", {"1.jpg": b"b2"})                           # 다른 게시글의 두 번째 사진과 같은 첫 사진
    d = _put(m.INBOX / "none_d.jpg", b"b1")                             # 파일 하나 게시글도 같은 문구
    new, errors = m.plan_add([a, b, c, d], {}, entries, m.IMAGES)
    assert [e["file"] for e in new] == ["none_b_p01.jpg", "none_b_p02.jpg"]
    assert sorted(errors) == sorted(["none_a: 첫 사진이 none_old.jpg 와 같은 사진이다",
                                     "none_c: 첫 사진이 none_b_p02.jpg 와 같은 사진이다",
                                     "none_d.jpg: 첫 사진이 none_b_p01.jpg 와 같은 사진이다"])


def test_plan_add_later_photo_duplicates_are_kept_with_dup_of(intake_mod):
    m = intake_mod
    _put(m.IMAGES / "none_old.jpg", b"old")
    entries = [_entry("none_old.jpg")]
    a = _post(m, "none_a", {"1.jpg": b"a1", "2.jpg": b"a1", "3.jpg": b"a1", "4.jpg": b"old"})
    b = _post(m, "none_b", {"1.jpg": b"b1", "2.jpg": b"a1"})
    new, errors = m.plan_add([a, b], {}, entries, m.IMAGES)
    assert errors == []
    assert {e["file"]: e.get("dup_of") for e in new} == {
        "none_a_p01.jpg": None, "none_a_p02.jpg": "none_a_p01.jpg", "none_a_p03.jpg": "none_a_p01.jpg",
        "none_a_p04.jpg": "none_old.jpg", "none_b_p01.jpg": None, "none_b_p02.jpg": "none_a_p01.jpg"}
    assert [e["post_index"] for e in new] == [1, 2, 3, 4, 1, 2]          # 중복이어도 자리는 그대로


def test_plan_add_failed_post_does_not_poison_later_checks(intake_mod):
    """오류 난 게시글의 사진 · 이름 · 주소는 '이미 있다'로 남지 않는다 — 다른 게시글에 엉뚱한 오류가 안 붙는다."""
    m = intake_mod
    entries = [_entry(f"inside_{i}.jpg", stratum="inside") for i in range(10)]   # inside 가득
    full = _post(m, "inside_x", {"1.jpg": b"same"})
    ok = _post(m, "none_y", {"1.jpg": b"same"})
    new, errors = m.plan_add([full, ok], {"inside_x": "https://u", "none_y": "https://u"}, entries, m.IMAGES)
    assert errors == ["inside_x: inside 층 목표(10개)를 넘는다 — 이 층은 다 모았다"]
    assert [e["file"] for e in new] == ["none_y_p01.jpg"]


def test_plan_add_same_url_with_existing_or_in_batch_is_error(intake_mod):
    m = intake_mod
    entries = [_entry("none_old.jpg", url="https://old"),
               _entry("wear_o_p02.jpg", stratum="", post="wear_o", post_index=2, url="https://o"),
               _entry("none_blank.jpg", url="")]
    a = _post(m, "none_a", {"1.jpg": b"a"})
    b = _put(m.INBOX / "none_b.jpg", b"b")
    c = _post(m, "none_c", {"1.jpg": b"c"})
    d = _post(m, "none_d", {"1.jpg": b"d"})
    e1 = _put(m.INBOX / "none_e.jpg", b"e")
    f1 = _put(m.INBOX / "none_f.jpg", b"f")                             # 둘 다 주소 없음 — 빈 주소는 안 본다
    urls = {"none_a": "https://old", "none_b.jpg": "https://o", "none_c": "https://new", "none_d": "https://new"}
    new, errors = m.plan_add([a, b, c, d, e1, f1], urls, entries, m.IMAGES)
    assert [e["file"] for e in new] == ["none_c_p01.jpg", "none_e.jpg", "none_f.jpg"]
    assert sorted(errors) == sorted([
        "none_a: 같은 게시글 주소가 이미 있다 (none_old.jpg) — 같은 판매자 · 같은 글은 하나만",
        "none_b.jpg: 같은 게시글 주소가 이미 있다 (wear_o) — 같은 판매자 · 같은 글은 하나만",
        "none_d: 같은 게시글 주소가 이미 있다 (none_c) — 같은 판매자 · 같은 글은 하나만"])


def test_follow_post_splits(intake_mod):
    m = intake_mod
    new = [{"file": "a1", "post": "a", "post_index": 1, "split": "test"},
           {"file": "a2", "post": "a", "post_index": 2},
           {"file": "b1", "post": "b", "post_index": 1, "split": "dev"},
           {"file": "b2", "post": "b", "post_index": 2, "split": "test"},   # 틀린 값이 있어도 덮는다
           {"file": "b3", "post": "b", "post_index": 3}]
    m.follow_post_splits(new)
    assert [e["split"] for e in new] == ["test", "test", "dev", "dev", "dev"]


# ── cmd_add ──
def test_add_folder_end_to_end(intake_mod, capsys):
    m = intake_mod
    _post(m, "wear_bag", {"10.JPG": b"10", "2.jpeg": b"2", "1.jpg": b"1", "3.jpg": b"3", "4.jpg": b"4",
                          "5.jpg": b"5", "6.jpg": b"6", "7.jpg": b"7", "8.jpg": b"8", "9.jpg": b"9",
                          ".DS_Store": b"x"})
    _put(m.INBOX / "none_one.png", b"one")
    (m.INBOX / "urls.txt").write_text("wear_bag https://x/bag\nnone_one https://x/one\n", encoding="utf-8")

    assert m.cmd_add(Namespace()) == 0
    out = capsys.readouterr().out
    entries = m.load_dataset()
    by = _by_file(entries)
    assert len(entries) == 11 and all("source" not in e for e in entries)    # 임시 칸은 저장 안 됨
    assert (m.IMAGES / "wear_bag_p02.jpeg").read_bytes() == b"2"
    assert (m.IMAGES / "wear_bag_p10.jpg").read_bytes() == b"10"             # 10.JPG → p10 · 소문자
    bag = [e for e in entries if e["post"] == "wear_bag"]
    assert len({e["split"] for e in bag}) == 1 and {e["url"] for e in bag} == {"https://x/bag"}
    assert by["none_one.png"]["url"] == "https://x/one"
    assert not (m.INBOX / "wear_bag").exists()                                # 숨김 파일이 있어도 폴더째
    assert not (m.INBOX / "none_one.png").exists()
    heads = [e for e in entries if not m.is_extra(e)]
    assert (f"게시글 2개 · 사진 11장 추가 (test {sum(e['split'] == 'test' for e in heads)} · "
            f"dev {sum(e['split'] == 'dev' for e in heads)})") in out
    assert "경고: urls.txt 에만" not in out and "주소가 없는" not in out
    assert sorted(r["file"] for r in _read_csv(m.LABELS_CSV)) == ["none_one.png", "wear_bag_p01.jpg"]
    log = [json.loads(ln) for ln in m.SPLIT_LOG.read_text(encoding="utf-8").splitlines()]
    assert {r["file"]: (r["split"], r["post"]) for r in log} == {e["file"]: (e["split"], e["post"]) for e in entries}
    assert {r["stratum"] for r in log if r["file"].startswith("wear_bag_p0") and r["file"] != "wear_bag_p01.jpg"} == {""}
    assert m.problems(entries, m.IMAGES, m.load_split_log()) == []


def test_add_over_post_max_moves_ten_and_deletes_rest(intake_mod, capsys):
    m = intake_mod
    _post(m, "none_big", _n(12))
    assert m.cmd_add(Namespace()) == 0
    assert "게시글 1개 · 사진 10장 추가" in capsys.readouterr().out
    assert sorted(p.name for p in m.IMAGES.iterdir() if p.is_file()) == [f"none_big_p{i:02d}.jpg" for i in range(1, 11)]
    assert {e["post_total"] for e in m.load_dataset()} == {12}
    assert not (m.INBOX / "none_big").exists()                                # 11 · 12 장은 옮기지 않고 지웠다


def test_add_split_same_within_every_post_and_heads_follow_slots(intake_mod):
    """게시글 12개 × 사진 3장 — test · dev 가 둘 다 나오고, 게시글 안에서는 언제나 같다."""
    m = intake_mod
    for i in range(12):
        _post(m, f"none_{i:02d}", _n(3, f"{i}-"))
    assert m.cmd_add(Namespace()) == 0
    entries = m.load_dataset()
    posts = {}
    for e in entries:
        posts.setdefault(e["post"], set()).add(e["split"])
    assert len(posts) == 12 and all(len(s) == 1 for s in posts.values())
    heads = [e for e in entries if e["post_index"] == 1]
    assert sorted(e["split"] for e in heads) == sorted(m.split_slots("none")[:12])   # 자리는 게시글 수만큼
    assert {e["split"] for e in heads} == {"dev", "test"}
    assert all(e["stratum"] == "" for e in entries if e["post_index"] > 1)


def test_add_pilot_folder_output_and_all_dev(intake_mod, capsys):
    m = intake_mod
    _post(m, "doc_p", _n(3))
    assert m.cmd_add(Namespace(pilot=True)) == 0
    assert "게시글 1개 · 사진 3장 추가 (파일럿 — 층 목표 밖) (test 0 · dev 1)" in capsys.readouterr().out
    es = m.load_dataset()
    assert all(e["split"] == "dev" and e["pilot"] is True for e in es)
    log = [json.loads(ln) for ln in m.SPLIT_LOG.read_text(encoding="utf-8").splitlines()]
    assert [r["stratum"] for r in log] == ["doc", "", ""]                     # legacy_stratum 은 첫 사진에만
    _post(m, "doc_q", {"1.jpg": b"q"})                                        # 본 수집은 첫 자리부터
    assert m.cmd_add(Namespace()) == 0
    assert m.load_dataset()[-1]["split"] == m.split_slots("doc")[0]


def test_add_later_batch_continues_slots_by_post_count(intake_mod):
    """기존 게시글의 추가 사진은 층 자리 수에 안 든다 — 다음 게시글이 k 번째 자리를 받는다."""
    m = intake_mod
    _post(m, "none_a", _n(3, "a"))
    assert m.cmd_add(Namespace()) == 0
    _post(m, "none_b", {"1.jpg": b"b1"})
    assert m.cmd_add(Namespace()) == 0
    by = _by_file(m.load_dataset())
    assert by["none_a_p01.jpg"]["split"] == m.split_slots("none")[0]
    assert by["none_b_p01.jpg"]["split"] == m.split_slots("none")[1]


def test_add_hidden_file_in_folder_does_not_block_next_add(intake_mod):
    """.DS_Store 만 남은 폴더가 inbox 에 남아 다음 add 를 '사진이 없다'로 막지 않는다."""
    m = intake_mod
    _post(m, "wear_b", {"1.jpg": b"b1", ".DS_Store": b"x"})
    assert m.cmd_add(Namespace()) == 0
    _put(m.INBOX / "none_c.jpg", b"c")
    assert m.cmd_add(Namespace()) == 0
    assert [e["file"] for e in m.load_dataset()] == ["wear_b_p01.jpg", "none_c.jpg"]


def test_add_error_leaves_inbox_and_dataset_untouched(intake_mod, capsys):
    m = intake_mod
    m.save_dataset([_entry("none_old.jpg", url="https://old")])
    _put(m.IMAGES / "none_old.jpg", b"old")
    _post(m, "none_ok", {"1.jpg": b"ok1", "2.jpg": b"ok1"})                   # 혼자면 통과 (두 번째 중복은 dup_of)
    _post(m, "none_big", {**_n(12, "g"), ".DS_Store": b"x"})                  # 혼자면 통과 (10장만)
    _post(m, "none_gap", {"1.jpg": b"x1", "3.jpg": b"x3"})
    _post(m, "none_dup", {"1.jpg": b"old"})
    _post(m, "none_url", {"1.jpg": b"u"})
    _put(m.INBOX / "none_old.png", b"x")                                     # 같은 게시글 이름
    (m.INBOX / "urls.txt").write_text("none_url https://old\n", encoding="utf-8")
    before = m.DATASET.read_text(encoding="utf-8")
    inbox_before = _tree(m.INBOX)

    assert m.cmd_add(Namespace()) == 1
    out = capsys.readouterr().out
    assert "아무것도 옮기지 않았다" in out
    assert "none_gap/: 번호가" in out and "none_dup: 첫 사진이 none_old.jpg 와 같은 사진이다" in out
    assert "none_url: 같은 게시글 주소가 이미 있다 (none_old.jpg)" in out
    assert "none_old.png: 같은 게시글 이름이 이미 있다" in out
    assert "none_ok" not in out and "none_big" not in out
    assert m.DATASET.read_text(encoding="utf-8") == before
    assert _tree(m.INBOX) == inbox_before                                    # 12장 · 숨김 파일까지 그대로
    assert sorted(p.name for p in m.IMAGES.iterdir()) == ["inbox", "none_old.jpg"]
    assert not m.SPLIT_LOG.exists() and not m.LABELS_CSV.exists()


def test_add_non_photo_or_subfolder_in_post_refuses_everything(intake_mod, capsys):
    m = intake_mod
    _post(m, "none_a", {"1.jpg": b"1", "2.heic": b"h"})
    _post(m, "none_b", {"1.jpg": b"b"})
    (m.INBOX / "none_b" / "more").mkdir()
    before = _tree(m.INBOX)
    assert m.cmd_add(Namespace()) == 1
    out = capsys.readouterr().out
    assert "none_a/2.heic" in out and "none_b/more" in out
    assert m.load_dataset() == [] and _tree(m.INBOX) == before


def test_add_save_failure_removes_copies_and_keeps_inbox(intake_mod, monkeypatch):
    m = intake_mod
    m.save_dataset([_entry("none_old.jpg")])
    before = m.DATASET.read_text(encoding="utf-8")
    _post(m, "none_a", {**_n(11), ".DS_Store": b"x"})
    _put(m.INBOX / "none_b.jpg", b"b")
    inbox_before = _tree(m.INBOX)

    def boom(entries):
        raise OSError("disk full")
    monkeypatch.setattr(m, "save_dataset", boom)
    with pytest.raises(OSError):
        m.cmd_add(Namespace())
    assert sorted(p.name for p in m.IMAGES.iterdir()) == ["inbox"]       # 복사본은 지웠다
    assert m.DATASET.read_text(encoding="utf-8") == before
    assert _tree(m.INBOX) == inbox_before                               # 폴더 · 11번째 · 숨김 파일 그대로
    assert not m.SPLIT_LOG.exists()


def test_add_copy_failure_midway_removes_earlier_copies(intake_mod, monkeypatch):
    m = intake_mod
    _post(m, "none_a", _n(3))
    real = m.shutil.copy2
    calls = []

    def flaky(src, dst):
        calls.append(dst)
        if len(calls) == 3:
            raise KeyboardInterrupt                                      # BaseException 도 정리
        return real(src, dst)
    monkeypatch.setattr(m.shutil, "copy2", flaky)
    with pytest.raises(KeyboardInterrupt):
        m.cmd_add(Namespace())
    assert sorted(p.name for p in m.IMAGES.iterdir()) == ["inbox"]
    assert not m.DATASET.exists()
    assert sorted(p.name for p in (m.INBOX / "none_a").iterdir()) == ["1.jpg", "2.jpg", "3.jpg"]


def test_add_with_old_entries_mixed(intake_mod, capsys):
    """옛 항목(post 칸 없음)과 섞여도 — 층 자리 수 · 라벨 CSV · 현황이 맞다."""
    m = intake_mod
    old = [_entry("none_old1.jpg", split="test"), _entry("none_old2.jpg", split="dev"),
           {"file": "legacy.webp", "photo_type": "product"}]
    m.save_dataset(old)
    _post(m, "none_new", {"1.jpg": b"1", "2.jpg": b"2"})
    assert m.cmd_add(Namespace()) == 0
    entries = m.load_dataset()
    assert entries[:3] == old                                            # 옛 항목은 그대로 (post 칸 안 생김)
    by = _by_file(entries)
    assert by["none_new_p01.jpg"]["split"] == m.split_slots("none")[2]   # 옛 none 2장 다음 자리
    assert by["none_new_p02.jpg"]["split"] == by["none_new_p01.jpg"]["split"]
    assert not m.is_extra(old[0]) and not m.is_extra(old[2])
    rows = [r["file"] for r in _read_csv(m.LABELS_CSV)]
    assert rows == ["none_old1.jpg", "none_old2.jpg", "legacy.webp", "none_new_p01.jpg"]
    table = m.status_table(entries, [])
    t = 1 + (by["none_new_p01.jpg"]["split"] == "test")
    assert f"| none | {t}/25 | {3 - t}/5 | 0 | 3 |" in table
    extra_split = by["none_new_p02.jpg"]["split"]
    assert (f"| (게시글 2번째 사진부터 · 장수) | {int(extra_split == 'test')} | {int(extra_split == 'dev')} | | — |"
            in table)
    assert "| (층 목표 밖 · 기존·파일럿) | 0 | 1 | | 1 |" in table           # legacy 만, 추가 사진은 안 섞인다
    # 옛 항목에는 post 칸이 없어 problems 의 게시글 검사에 안 걸린다
    assert not [p for p in m.problems(entries, m.IMAGES, m.load_split_log()) if "게시글" in p]


def test_add_urls_stray_check_knows_folders_stems_and_existing_posts(intake_mod, capsys):
    m = intake_mod
    m.save_dataset([_entry("wear_old_p01.jpg", post="wear_old", post_index=1)])
    _post(m, "none_a", {"1.jpg": b"a"})
    _put(m.INBOX / "none_b.jpg", b"b")
    _put(m.INBOX / "none_c.jpg", b"c")
    (m.INBOX / "urls.txt").write_text(
        "none_a https://a\nnone_b https://b\nnone_c.jpg https://c\nwear_old https://o\n"
        "wear_old_p01.jpg https://o1\nghost https://g\n", encoding="utf-8")
    assert m.cmd_add(Namespace()) == 0
    out = capsys.readouterr().out
    assert "경고: urls.txt 에만 있고 inbox 에 없는 이름 — ghost" in out
    by = _by_file(m.load_dataset())
    assert (by["none_a_p01.jpg"]["url"], by["none_b.jpg"]["url"], by["none_c.jpg"]["url"]) == \
        ("https://a", "https://b", "https://c")


# ── is_extra · 라벨 · 현황 · problems ──
@pytest.mark.parametrize("entry, extra", [
    ({}, False), ({"post_index": None}, False), ({"post_index": 0}, False),
    ({"post_index": 1}, False), ({"post_index": 2}, True), ({"post_index": 10}, True)])
def test_is_extra(intake_mod, entry, extra):
    assert intake_mod.is_extra({"file": "x.jpg", **entry}) is extra


def test_needs_label_false_for_extras_even_unlabeled_or_draft(intake_mod):
    m = intake_mod
    assert m.needs_label(_entry("a_p01.jpg", post_index=1)) is True
    assert m.needs_label(_entry("a_p02.jpg", post_index=2)) is False
    assert m.needs_label(_entry("a_p03.jpg", post_index=3, labeled=True, labeled_by=m.DRAFT)) is False
    assert m.needs_label(_entry("old.jpg")) is True                       # 옛 항목 = 첫 사진


def test_write_labels_csv_skips_extras_even_with_include_labeled(intake_mod):
    m = intake_mod
    entries = [_entry("a_p01.jpg", post="a", post_index=1), _entry("a_p02.jpg", stratum="", post="a", post_index=2),
               _entry("b_p01.jpg", labeled=True, labeled_by="철수", post="b", post_index=1),
               _entry("b_p02.jpg", stratum="", labeled=True, labeled_by="철수", post="b", post_index=2),
               _entry("old.jpg")]
    assert m.write_labels_csv(entries, m.LABELS_CSV) == 2
    assert [r["file"] for r in _read_csv(m.LABELS_CSV)] == ["a_p01.jpg", "old.jpg"]
    assert m.write_labels_csv(entries, m.LABELS_CSV, include_labeled=True) == 3
    assert [r["file"] for r in _read_csv(m.LABELS_CSV)] == ["a_p01.jpg", "b_p01.jpg", "old.jpg"]


def test_status_table_extras_row(intake_mod):
    m = intake_mod
    entries = [_entry("none_a_p01.jpg", split="test", post_index=1),
               _entry("none_a_p02.jpg", stratum="", split="test", post_index=2),
               _entry("none_b_p01.jpg", split="dev", post_index=1),
               _entry("none_b_p02.jpg", stratum="", split="dev", post_index=2),
               _entry("none_c_p02.jpg", stratum="", split=None, post_index=2),           # split 없음 → dev
               _entry("doc_p_p02.jpg", stratum="", split="dev", post_index=2, pilot=True)]
    table = m.status_table(entries, ["none_x", "wear_y", "none_z.jpg"])
    assert "| none | 1/25 | 1/5 | 2 | 2 |" in table
    assert "| wear | 0/35 | 0/8 | 1 | 0 |" in table
    assert "| (게시글 2번째 사진부터 · 장수) | 1 | 3 | | — |" in table
    assert "층 목표 밖" not in table                                     # 추가 사진뿐이면 그 줄이 없다
    assert "게시글 2번째" not in m.status_table(entries[:1], [])


def test_tag_table_ignores_extras(intake_mod):
    m = intake_mod
    es = [_entry("none_a_p01.jpg", post_index=1, edge_tags=["pattern"]),
          _entry("none_a_p02.jpg", stratum="", post_index=2, edge_tags=["pattern"], item_count=3),
          _entry("s_p02.jpg", stratum="", post_index=2, supplement=True, edge_tags=["pattern"]),
          _entry("p_p02.jpg", stratum="", post_index=2, pilot=True, edge_tags=["pattern"])]
    assert _tag_line(m.tag_table(es), "pattern") == "| pattern | gen | 1 | 0 | 5 ⚠ | 0 |"
    assert m.tag_table(es).splitlines()[-1] == "| multi (item_count ≥ 2) | cut | 0 | 0 | 5 ⚠ | 0 |"


def test_problems_split_divided_inside_post(intake_mod):
    m = intake_mod
    entries = [_entry("a_p01.jpg", split="test", post="a", post_index=1),
               _entry("a_p02.jpg", split="dev", post="a", post_index=2),
               _entry("b_p01.jpg", split="dev", post="b", post_index=1),
               _entry("b_p02.jpg", split=None, post="b", post_index=2),       # 없음 = dev → 갈린 게 아니다
               _entry("c_p01.jpg", split=None, post="c", post_index=1),
               _entry("c_p02.jpg", split="test", post="c", post_index=2),
               _entry("x.jpg", split="test"), _entry("y.jpg", split="dev")]   # post 칸 없는 옛 항목끼리는 안 본다
    got = [p for p in m.problems(entries, m.IMAGES, {}) if "게시글" in p]
    assert got == ["게시글 안에서 split 이 갈렸다: a (dev · test)", "게시글 안에서 split 이 갈렸다: c (dev · test)"]


def test_cmd_status_lists_post_folders_and_split_problem(intake_mod, capsys):
    m = intake_mod
    m.save_dataset([_entry("none_a_p01.jpg", split="test", post="none_a", post_index=1),
                    _entry("none_a_p02.jpg", stratum="", split="dev", post="none_a", post_index=2)])
    _post(m, "wear_a", {"1.jpg": b"1", "2.jpg": b"2", "x.txt": b"t"})
    _post(m, "bad", {"1.jpg": b"b"})
    assert m.cmd_status(Namespace()) == 0
    out = capsys.readouterr().out
    assert "| wear | 0/35 | 0/8 | 1 | 0 |" in out                          # 사진 2장이어도 게시글 1개
    assert "이름이 규칙에 안 맞는 inbox 사진: bad" in out
    assert "wear_a/x.txt" in out
    assert "확인 필요: 게시글 안에서 split 이 갈렸다: none_a (dev · test)" in out
