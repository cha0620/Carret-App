"""eval/intake.py — inbox → dataset.json, split 배정, 라벨 CSV 왕복."""
import csv
import json
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


# ── split ──
def _new(n, stratum="none"):
    return [{"file": f"{stratum}_{i:03d}.jpg", "stratum": stratum} for i in range(n)]


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


# ── tag_table · status ──
def _tag_line(table, name):
    return next(ln for ln in table.splitlines() if ln.startswith(f"| {name} |"))


def test_tag_table_string_edge_tags_is_one_tag(intake_mod):
    """edge_tags 가 문자열이면 글자 단위가 아니라 통째 한 태그 ("tag" 가 "packaging" 안에서 잡히면 안 된다)."""
    m = intake_mod
    table = m.tag_table([_entry("none_a.jpg", edge_tags="packaging")])
    assert _tag_line(table, "packaging") == "| packaging | gen | 1 | 0 | 5 ⚠ | 0 |"
    assert _tag_line(table, "tag") == "| tag | gen | 0 | 0 |  | 0 |"


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


def test_plan_add_generated_name_clash_inside_one_batch(intake_mod):
    """폴더 none_a/1.jpg → none_a_p01.jpg 와 파일 하나 none_a_p01.jpg — 같은 add 안에서도 잡는다."""
    m = intake_mod
    folder = _post(m, "none_a", {"1.jpg": b"folder"})
    single = _put(m.INBOX / "none_a_p01.jpg", b"single")
    new, errors = m.plan_add([single, folder], {}, [], m.IMAGES)
    assert [e["file"] for e in new] == ["none_a_p01.jpg"] and new[0]["post"] == "none_a"
    assert errors == ["none_a_p01.jpg: 같은 이름이 이미 있다"]


def test_plan_add_failed_post_does_not_poison_later_checks(intake_mod):
    """오류 난 게시글의 사진 · 이름 · 주소는 '이미 있다'로 남지 않는다 — 다른 게시글에 엉뚱한 오류가 안 붙는다."""
    m = intake_mod
    entries = [_entry(f"inside_{i}.jpg", stratum="inside") for i in range(10)]   # inside 가득
    full = _post(m, "inside_x", {"1.jpg": b"same"})
    ok = _post(m, "none_y", {"1.jpg": b"same"})
    new, errors = m.plan_add([full, ok], {"inside_x": "https://u", "none_y": "https://u"}, entries, m.IMAGES)
    assert errors == ["inside_x: inside 층 목표(10개)를 넘는다 — 이 층은 다 모았다"]
    assert [e["file"] for e in new] == ["none_y_p01.jpg"]


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




def test_parse_label_row_accepts_pack_category(intake_mod):
    vals, errors, _ = intake_mod.parse_label_row(
        {"category": "pack", "photo_type": "product", "wear_level": "none", "text_level": "none"})
    assert errors == [] and vals["category"] == "pack"
