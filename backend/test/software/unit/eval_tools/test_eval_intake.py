"""eval/intake.py — inbox → dataset.json, split 배정, 라벨 CSV 왕복."""
import csv
from argparse import Namespace


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
    assert "urls.txt 에 주소가 없는 사진 1장: doc_b.png" in capsys.readouterr().out


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
    assert "none_dup.jpg: none_old.jpg 와 같은 사진이다" in out
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
        "none_q.jpg: none_p.jpg 와 같은 사진이다"])


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
    assert m.split_slots("heavy").count("dev") == 3


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
    rows[0].update(photo_type="product", wear_level="light", text_level="simple",
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
    good = {"file": "none_a.jpg", "photo_type": "product", "wear_level": "none", "text_level": "none"}
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
        w.writerow({"file": "none_a.jpg", "photo_type": "document", "wear_level": "none",
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
    rows[0].update(photo_type="product", key_texts="SONY")
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
    assert "inside 층 목표(10장)를 넘는다" in capsys.readouterr().out
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
    p.write_text("file,photo_type,wear_level,text_level,key_texts,item\n"
                 "none_a.jpg,Product,None,SIMPLE,2024-01-02|가방,백\n,,,,,\n", encoding="cp949")
    rows = m.read_csv_rows(p)
    n, errors, warnings = m.merge_labels(entries, rows, "x")
    assert (n, errors) == (1, [])
    assert entries[0]["photo_type"] == "product" and entries[0]["item"] == "백"
    assert len(warnings) == 1 and "2024-01-02" in warnings[0]


def test_merge_rejects_duplicate_rows_and_changing_human_test_label(intake_mod):
    m = intake_mod
    done = _entry("none_t.jpg", split="test", labeled=True, labeled_by="철수")
    entries = [done, _entry("none_a.jpg")]
    row = {"file": "none_a.jpg", "photo_type": "product", "wear_level": "none", "text_level": "none"}
    _, errors, _ = m.merge_labels(entries, [row, dict(row)], "x")
    assert errors == ["none_a.jpg: 두 줄 있다"]
    change = {"file": "none_t.jpg", "photo_type": "document", "wear_level": "none", "text_level": "none"}
    _, errors, _ = m.merge_labels(entries, [change], "영희")
    assert "--relabel" in errors[0] and done["photo_type"] == "product"
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
