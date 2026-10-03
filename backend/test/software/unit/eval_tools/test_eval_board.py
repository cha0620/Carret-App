"""eval/board.py — 결과판 (메모·결과 읽기 · 사진별 모으기/정렬 · 실패 모음 저장 · 파일 열기 · HTML · 서버)."""
import contextlib
import csv
import http.client
import json
import sys
import threading
from datetime import date
from http.server import ThreadingHTTPServer

import pytest

from conftest import EVAL_DIR

COLS = ["file", "repeat", "mode", "reviewed", "shape_color_changed", "text_changed", "wear_changed",
        "added_content", "background_issue", "framing_issue", "failure_tags", "photo_quality", "note"]
XSS = "<script>alert(1)</script>"


# ── 도우미 ──────────────────────────────────────
def R(file, repeat=1, **kw):
    row = {"file": file, "repeat": repeat, "mode": "generate", "orig": f"files/orig_{file}",
           "result": f"files/out_{file}"}
    row.update(kw)
    return row


def write_run(root, run_id, results, raw_extra="", images=()):
    d = root / "results" / "runs" / run_id
    (d / "files").mkdir(parents=True, exist_ok=True)
    (d / "results.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results) + raw_extra, encoding="utf-8")
    for rel in images:
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_bytes(b"IMG")


def write_review(root, run_id, rater, rows, raw_extra=""):
    d = root / "results" / "reviews" / run_id
    d.mkdir(parents=True, exist_ok=True)
    with open(d / f"{rater}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in COLS})
        f.write(raw_extra)


def ok(file, repeat=1, **kw):
    """채점 줄 — 기본은 '봤음, 보존 통과'."""
    return {"file": file, "repeat": repeat, "reviewed": "1", **kw}


def bad(file, repeat=1, **kw):
    return ok(file, repeat, shape_color_changed="1", **kw)


def write_dataset(mod, entries):
    mod.intake.DATASET.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")


def read_dataset(mod):
    return json.loads(mod.intake.DATASET.read_text(encoding="utf-8"))


# ── import 방식 ─────────────────────────────────
def test_import_puts_eval_dir_on_sys_path_and_paths_are_redirected(board_mod, tmp_path):
    assert str(EVAL_DIR) in sys.path
    assert board_mod.rp.__file__ == str(EVAL_DIR / "report.py")
    assert board_mod.intake.__file__ == str(EVAL_DIR / "intake.py")
    assert board_mod.HERE == tmp_path and board_mod.rp.HERE == tmp_path
    assert board_mod.intake.DATASET == tmp_path / "data" / "dataset.json"
    assert board_mod.intake.load_dataset() == []          # 실제 dataset.json 이 아니다


# ── _notes ──────────────────────────────────────
def test_notes_collects_per_rater_skips_template_and_empty(board_mod, tmp_path):
    write_review(tmp_path, "r1", "kim", [ok("a.webp", note="배경 얼룩"), ok("b.webp", note="  ")])
    write_review(tmp_path, "r1", "lee", [bad("a.webp", note="로고 바뀜")])
    write_review(tmp_path, "r1", "TEMPLATE", [ok("a.webp", note="템플릿 예시")])
    n = board_mod._notes("r1")
    assert n[("a.webp", 1)] == ["kim: 배경 얼룩", "lee: 로고 바뀜"]
    assert ("b.webp", 1) not in n                          # 공백만 있는 메모는 없음


def test_notes_only_reviewed_rows(board_mod, tmp_path):
    write_review(tmp_path, "r1", "kim", [{"file": "a.webp", "repeat": 1, "note": "아직 안 봄"},
                                         {"file": "b.webp", "repeat": 1, "reviewed": "no", "note": "no"},
                                         ok("c.webp", note="봄")])
    assert dict(board_mod._notes("r1")) == {("c.webp", 1): ["kim: 봄"]}


def test_notes_skips_broken_rows(board_mod, tmp_path):
    write_review(tmp_path, "r1", "kim", [ok("a.webp", note="정상")],
                 raw_extra="b.webp,abc,,1,,,,,,,,,깨진 repeat\nc.webp,,,1,,,,,,,,,빈 repeat\n")
    assert dict(board_mod._notes("r1")) == {("a.webp", 1): ["kim: 정상"]}


def test_notes_missing_repeat_column_and_bom(board_mod, tmp_path):
    d = tmp_path / "results" / "reviews" / "r1"
    d.mkdir(parents=True)
    (d / "kim.csv").write_text("﻿file,reviewed,note\na.webp,1,repeat 없음\n", encoding="utf-8")
    (d / "lee.csv").write_text("﻿file,repeat,reviewed,note\na.webp,2,1,BOM 있음\n", encoding="utf-8")
    assert dict(board_mod._notes("r1")) == {("a.webp", 2): ["lee: BOM 있음"]}


def test_notes_ignores_undecodable_csv(board_mod, tmp_path):
    write_review(tmp_path, "r1", "kim", [ok("a.webp", note="정상")])
    (tmp_path / "results" / "reviews" / "r1" / "broken.csv").write_bytes(b"file,repeat,reviewed,note\n\xff\xfe\xfa,1,1,x\n")
    assert dict(board_mod._notes("r1")) == {("a.webp", 1): ["kim: 정상"]}


def test_notes_no_review_dir(board_mod):
    assert dict(board_mod._notes("없는실행")) == {}


# ── _results ────────────────────────────────────
def test_results_skips_broken_blank_and_non_dict_lines(board_mod, tmp_path):
    write_run(tmp_path, "r1", [R("a.webp")], raw_extra='\n   \n[1, 2]\n"str"\nnull\n{"file": "cut', )
    assert board_mod._results("r1") == [R("a.webp")]


def test_results_missing_or_undecodable(board_mod, tmp_path):
    assert board_mod._results("없음") == []
    d = tmp_path / "results" / "runs" / "bin"
    d.mkdir(parents=True)
    (d / "results.jsonl").write_bytes(b"\xff\xfe\x00")
    assert board_mod._results("bin") == []


# ── collect ─────────────────────────────────────
def test_collect_skips_analyze_only_run(board_mod, tmp_path):
    write_dataset(board_mod, [{"file": "a.webp"}])
    write_run(tmp_path, "r1", [{"file": "a.webp", "repeat": 1, "mode": "analyze", "orig": "files/orig_a.webp"}],
              images=["files/orig_a.webp"])
    [it] = board_mod.collect()
    assert it == {"file": "a.webp", "entry": {"file": "a.webp"}, "orig": None, "results": []}


def test_collect_keeps_error_rows_without_verdict(board_mod, tmp_path):
    write_dataset(board_mod, [{"file": "a.webp"}, {"file": "b.webp"}])
    write_run(tmp_path, "r1", [R("a.webp"), R("b.webp", result=None, error="timeout")],
              images=["files/orig_b.webp"])
    # 오류난 회차에 옛 채점 줄이 남아 있어도 판정 · 평가자 · 메모를 붙이지 않는다
    write_review(tmp_path, "r1", "kim", [bad("b.webp", note="옛 메모"), ok("a.webp")])
    items = {it["file"]: it for it in board_mod.collect()}
    [b] = items["b.webp"]["results"]
    assert b["error"] == "timeout" and b["result"] is None
    assert (b["preserved"], b["raters"], b["notes"], b["flags"], b["tags"]) == (None, [], [], [], [])
    assert items["b.webp"]["orig"] == ("r1", "files/orig_b.webp")
    assert items["a.webp"]["results"][0]["preserved"] is True


def test_collect_skips_rows_with_bad_file_or_repeat_and_duplicates(board_mod, tmp_path):
    write_run(tmp_path, "r1", [
        R("a.webp", mode="first"),
        R("a.webp", mode="dup"),                 # 같은 (file, repeat) — 무시
        R("a.webp", repeat=2),
        {"repeat": 1, "result": "x"},            # file 없음
        {"file": 3, "repeat": 1, "result": "x"},  # file 이 문자열 아님
        {"file": "c.webp", "result": "x"},       # repeat 없음
        {"file": "d.webp", "repeat": "1", "result": "x"},   # repeat 이 정수 아님
    ])
    items = {it["file"]: it for it in board_mod.collect()}
    assert set(items) == {"a.webp"}
    assert [(x["repeat"], x["mode"]) for x in items["a.webp"]["results"]] == [(1, "first"), (2, "generate")]


def test_collect_majority_tie_flags_quality_tags_raters_notes(board_mod, tmp_path):
    write_dataset(board_mod, [{"file": "a.webp", "item": "신발"}, {"file": "b.webp"}])
    write_run(tmp_path, "r1", [R("a.webp"), R("b.webp")])
    write_review(tmp_path, "r1", "kim", [ok("a.webp", failure_tags="blur", background_issue="1"),
                                         bad("b.webp", note="색 바뀜")])
    write_review(tmp_path, "r1", "lee", [bad("a.webp", text_changed="1", framing_issue="y", failure_tags="logo; blur"),
                                         ok("b.webp")])
    write_review(tmp_path, "r1", "park", [ok("a.webp")])
    items = {it["file"]: it for it in board_mod.collect()}
    a = items["a.webp"]["results"][0]
    assert a["preserved"] is True                                 # 2:1
    assert a["flags"] == ["shape_color_changed", "text_changed"]  # 물건 표시만
    assert a["quality_flags"] == ["background_issue", "framing_issue"]
    assert a["tags"] == ["blur", "logo"]
    assert a["raters"] == ["kim", "lee", "park"]
    assert items["a.webp"]["entry"] == {"file": "a.webp", "item": "신발"}
    b = items["b.webp"]["results"][0]
    assert b["preserved"] is False                                # 1:1 동점은 실패
    assert b["raters"] == ["kim", "lee"] and b["notes"] == ["kim: 색 바뀜"]


def test_collect_quality_only_flags_still_preserved(board_mod, tmp_path):
    write_run(tmp_path, "r1", [R("a.webp")])
    write_review(tmp_path, "r1", "kim", [ok("a.webp", background_issue="1")])
    x = board_mod.collect()[0]["results"][0]
    assert x["preserved"] is True and x["flags"] == [] and x["quality_flags"] == ["background_issue"]


def test_collect_unreviewed_row_is_not_a_vote(board_mod, tmp_path):
    write_dataset(board_mod, [{"file": "a.webp"}])
    write_run(tmp_path, "r1", [R("a.webp")])
    write_review(tmp_path, "r1", "kim", [{"file": "a.webp", "repeat": 1, "shape_color_changed": "1"}])
    [it] = board_mod.collect()
    assert it["results"][0]["preserved"] is None and it["results"][0]["raters"] == []


def test_collect_survives_undecodable_review_csv(board_mod, tmp_path):
    write_run(tmp_path, "r1", [R("a.webp")])
    write_review(tmp_path, "r1", "kim", [bad("a.webp")])
    (tmp_path / "results" / "reviews" / "r1" / "broken.csv").write_bytes(b"file,repeat,reviewed\n\xff\xfe,1,1\n")
    [it] = board_mod.collect()                                    # 예외 없이 열린다
    assert len(it["results"]) == 1


def test_collect_includes_unrun_dataset_photos_and_unknown_files(board_mod, tmp_path):
    write_dataset(board_mod, [{"file": "never.webp", "set": "failure"}])
    write_run(tmp_path, "r1", [R("ghost.webp")])
    items = {it["file"]: it for it in board_mod.collect()}
    assert items["never.webp"] == {"file": "never.webp", "entry": {"file": "never.webp", "set": "failure"},
                                   "orig": None, "results": []}
    assert items["ghost.webp"]["entry"] == {} and len(items["ghost.webp"]["results"]) == 1


def test_collect_orig_only_if_real_image_under_files(board_mod, tmp_path):
    write_run(tmp_path, "r1", [R("a.webp", orig=None), R("a.webp", 2, orig="files/missing.webp"),
                               R("a.webp", 3, orig="results.jsonl"), R("a.webp", 4, orig="files/o1.webp")],
              images=["files/o1.webp"])
    write_run(tmp_path, "r2", [R("a.webp", orig="files/o2.webp")], images=["files/o2.webp"])
    (tmp_path / "results" / "runs" / "no_results").mkdir(parents=True)
    [it] = board_mod.collect()
    assert [(x["run"], x["repeat"]) for x in it["results"]] == [("r1", 1), ("r1", 2), ("r1", 3), ("r1", 4), ("r2", 1)]
    assert it["orig"] == ("r1", "files/o1.webp")


def test_collect_sort_order(board_mod, tmp_path):
    names = ["half.webp", "all_bad.webp", "one_of_three.webp", "two_of_four.webp", "clean.webp",
             "unrated.webp", "not_run.webp", "a_unrated.webp"]
    write_dataset(board_mod, [{"file": n} for n in names])
    write_run(tmp_path, "r1", [R("half.webp", 1), R("half.webp", 2), R("all_bad.webp"),
                               R("one_of_three.webp", 1), R("one_of_three.webp", 2), R("one_of_three.webp", 3),
                               R("two_of_four.webp", 1), R("two_of_four.webp", 2), R("two_of_four.webp", 3),
                               R("two_of_four.webp", 4),
                               R("clean.webp"), R("unrated.webp"), R("a_unrated.webp")])
    write_review(tmp_path, "r1", "kim", [
        bad("half.webp", 1), ok("half.webp", 2), bad("all_bad.webp"),
        bad("one_of_three.webp", 1), ok("one_of_three.webp", 2), ok("one_of_three.webp", 3),
        bad("two_of_four.webp", 1), bad("two_of_four.webp", 2), ok("two_of_four.webp", 3), ok("two_of_four.webp", 4),
        ok("clean.webp")])
    order = [it["file"] for it in board_mod.collect()]
    # 1.0 → 0.5(실패 2개가 1개보다 앞) → 0.33 → 0.0 → 채점 없음(이름순)
    assert order == ["all_bad.webp", "two_of_four.webp", "half.webp", "one_of_three.webp", "clean.webp",
                     "a_unrated.webp", "not_run.webp", "unrated.webp"]


def test_collect_empty(board_mod):
    assert board_mod.collect() == []


# ── set_failure ─────────────────────────────────
@pytest.fixture
def lock_calls(board_mod, monkeypatch):
    """intake.dataset_lock 을 감싸 save_dataset 이 락 안에서 불리는지 기록."""
    calls = []
    real_lock, real_save = board_mod.intake.dataset_lock, board_mod.intake.save_dataset
    depth = [0]

    @contextlib.contextmanager
    def lock():
        with real_lock():
            depth[0] += 1
            calls.append("lock")
            try:
                yield
            finally:
                depth[0] -= 1

    def save(entries):
        calls.append("save-in-lock" if depth[0] else "save-outside")
        real_save(entries)

    monkeypatch.setattr(board_mod.intake, "dataset_lock", lock)
    monkeypatch.setattr(board_mod.intake, "save_dataset", save)
    return calls


def test_set_failure_on_sets_set_and_date_inside_lock(board_mod, lock_calls):
    write_dataset(board_mod, [{"file": "a.webp", "item": "컵", "note": "근거"}, {"file": "b.webp"}])
    assert board_mod.set_failure("a.webp", True) is True
    a, b = read_dataset(board_mod)
    assert a == {"file": "a.webp", "item": "컵", "note": "근거", "set": "failure",
                 "failure_added": date.today().isoformat()}
    assert b == {"file": "b.webp"}
    assert lock_calls == ["lock", "save-in-lock"]


def test_set_failure_on_again_does_not_write(board_mod, lock_calls):
    write_dataset(board_mod, [{"file": "a.webp", "set": "failure", "failure_added": "2026-01-01"}])
    before = board_mod.intake.DATASET.read_text(encoding="utf-8")
    assert board_mod.set_failure("a.webp", True) is True
    assert board_mod.intake.DATASET.read_text(encoding="utf-8") == before   # 날짜 유지 · 서식도 그대로
    assert lock_calls == ["lock"]


def test_set_failure_on_overrides_other_set(board_mod):
    write_dataset(board_mod, [{"file": "a.webp", "set": "dev"}])
    board_mod.set_failure("a.webp", True)
    e = read_dataset(board_mod)[0]
    assert e["set"] == "failure" and e["failure_added"] == date.today().isoformat()


def test_set_failure_off_removes_only_set_and_date(board_mod, lock_calls):
    write_dataset(board_mod, [{"file": "a.webp", "set": "failure", "failure_added": "2026-01-01",
                               "failure_tags": ["logo"], "note": "근거 메모", "split": "test"}])
    assert board_mod.set_failure("a.webp", False) is True
    assert read_dataset(board_mod) == [{"file": "a.webp", "failure_tags": ["logo"], "note": "근거 메모",
                                        "split": "test"}]
    assert lock_calls == ["lock", "save-in-lock"]


@pytest.mark.parametrize("entry", [{"file": "a.webp", "item": "컵"},
                                   {"file": "a.webp", "set": "dev", "failure_added": "2026-01-01"}])
def test_set_failure_off_leaves_non_failure_untouched(board_mod, lock_calls, entry):
    write_dataset(board_mod, [entry])
    before = board_mod.intake.DATASET.read_text(encoding="utf-8")
    assert board_mod.set_failure("a.webp", False) is True
    assert board_mod.intake.DATASET.read_text(encoding="utf-8") == before
    assert "save-in-lock" not in lock_calls


def test_set_failure_unknown_file_returns_false_and_does_not_write(board_mod, lock_calls):
    write_dataset(board_mod, [{"file": "a.webp"}])
    before = board_mod.intake.DATASET.read_text(encoding="utf-8")
    assert board_mod.set_failure("zzz.webp", True) is False
    assert board_mod.intake.DATASET.read_text(encoding="utf-8") == before
    assert lock_calls == ["lock"]


def test_set_failure_no_dataset_file(board_mod):
    assert board_mod.set_failure("a.webp", True) is False
    assert not board_mod.intake.DATASET.exists()


def test_set_failure_concurrent_writes_do_not_lose_updates(board_mod, tmp_path):
    write_dataset(board_mod, [{"file": f"{i}.webp"} for i in range(20)])
    ts = [threading.Thread(target=board_mod.set_failure, args=(f"{i}.webp", True)) for i in range(20)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert all(e.get("set") == "failure" for e in read_dataset(board_mod))
    assert not list(tmp_path.glob(".dataset-*.tmp"))              # 임시 파일이 남지 않는다


# ── _image · static_file ────────────────────────
@pytest.fixture
def run_files(board_mod, tmp_path):
    runs = tmp_path / "results" / "runs"
    (runs / "r1" / "files" / "sub").mkdir(parents=True)
    for rel in ("files/a.webp", "files/b.JPG", "files/sub/c.png", "files/note.txt", "orig.webp",
                "review.html", "report.md", "results.jsonl", "carret.db", "storage/x.webp"):
        (runs / "r1" / rel).parent.mkdir(parents=True, exist_ok=True)
        (runs / "r1" / rel).write_bytes(b"DATA " + rel.encode())
    (runs / "compare-r1-r2.html").write_text("cmp", encoding="utf-8")
    (runs / "failures.html").write_text("fail", encoding="utf-8")
    (runs / "notes.txt").write_text("x")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.webp").write_bytes(b"SECRET")
    (outside / "review.html").write_text("SECRET")
    (outside / "x.html").write_text("SECRET")
    return runs


def test_image_allows_only_real_images_under_files(board_mod, run_files):
    img = board_mod._image
    assert img("r1", "files/a.webp") == run_files / "r1" / "files" / "a.webp"
    assert img("r1", "files/b.JPG") is not None                 # 확장자 대소문자 무시
    assert img("r1", "files/sub/c.png") is not None
    for rel in ("files/note.txt", "files/none.webp", "orig.webp", "storage/x.webp", "results.jsonl",
                "files/../orig.webp", "files/../../r1/files/../carret.db", "", None, "files", "files/sub"):
        assert img("r1", rel) is None, rel
    for run in ("", "..", ".", "r1/files", "없음"):
        assert img(run, "files/a.webp") is None, run


def test_image_absolute_rel_is_blocked(board_mod, run_files):
    abs_path = str(run_files / "r1" / "files" / "a.webp")
    # 절대경로는 Path 결합에서 그대로 쓰이지만, 여기선 진짜로 files/ 아래라 허용된다 — 밖이면 막힌다
    assert board_mod._image("r1", str(run_files.parent / "outside" / "secret.webp")) is None
    assert board_mod._image("r1", abs_path) is not None


def test_image_symlink_pointing_outside_is_blocked(board_mod, run_files, tmp_path):
    (run_files / "r1" / "files" / "link.webp").symlink_to(tmp_path / "outside" / "secret.webp")
    assert board_mod._image("r1", "files/link.webp") is None


def test_static_file_allowed(board_mod, run_files):
    sf = board_mod.static_file
    assert sf("/compare-r1-r2.html") == run_files / "compare-r1-r2.html"
    assert sf("/failures.html") == run_files / "failures.html"
    assert sf("/r1/review.html") == run_files / "r1" / "review.html"
    assert sf("/r1/report.md") == run_files / "r1" / "report.md"
    assert sf("/r1/files/a.webp") == run_files / "r1" / "files" / "a.webp"
    assert sf("/r1/files/sub/c.png") == run_files / "r1" / "files" / "sub" / "c.png"
    assert sf("//r1//review.html") is not None                  # 빈 조각은 무시


@pytest.mark.parametrize("path", [
    "/", "/r1", "/r1/", "/r1/files", "/r1/files/", "/r1/files/sub", "/r1/results.jsonl", "/r1/carret.db",
    "/r1/storage/x.webp", "/r1/orig.webp", "/r1/files/note.txt", "/notes.txt", "/none.html",
    "/r1/files/../results.jsonl", "/r1/files/..%2Fresults.jsonl", "/..%2Fdataset.json", "/../dataset.json",
    "/../outside/x.html", "/r1/../../outside/review.html", "/../review.html", "/./review.html",
    "/r1/sub/review.html", "/r1/files/../../outside/secret.webp", "/%2e%2e/outside/review.html",
])
def test_static_file_blocked(board_mod, run_files, path):
    assert board_mod.static_file(path) is None


def test_static_file_symlinks_outside_blocked(board_mod, run_files, tmp_path):
    out = tmp_path / "outside"
    (run_files / "evil.html").symlink_to(out / "x.html")
    (run_files / "r1" / "files" / "evil.webp").symlink_to(out / "secret.webp")
    (run_files / "r2").mkdir()
    (run_files / "r2" / "review.html").symlink_to(out / "review.html")
    (run_files / "r3").symlink_to(out, target_is_directory=True)       # 실행 폴더 자체가 밖을 가리킴
    for path in ("/evil.html", "/r1/files/evil.webp", "/r2/review.html", "/r3/review.html"):
        assert board_mod.static_file(path) is None, path


# ── _src ────────────────────────────────────────
@pytest.mark.parametrize("rel", ["", None, "/etc/passwd", "\\windows\\x", "../dataset.json", "a/../../x",
                                 "a/.."])
def test_src_blocks_absolute_and_parent(board_mod, rel):
    assert board_mod._src("r1", rel) is None


def test_src_plain_and_encoding(board_mod):
    assert board_mod._src("r1", "files/a.webp") == "/r1/files/a.webp"
    assert board_mod._src("r 1", "a b&c\"<.webp") == "/r%201/a%20b%26c%22%3C.webp"
    assert board_mod._src("r1", "신발.webp") == "/r1/%EC%8B%A0%EB%B0%9C.webp"
    assert board_mod._src("r1", "a..b.webp") == "/r1/a..b.webp"           # 이름 안의 .. 는 괜찮다
    assert board_mod._src("r1", 3) == "/r1/3"


# ── page ────────────────────────────────────────
def _item(file="a.webp", entry=None, orig=("r1", "files/orig_a.webp"), results=()):
    return {"file": file, "entry": entry if entry is not None else {"file": file}, "orig": orig,
            "results": list(results)}


def _res(run="r1", repeat=1, mode="generate", result="files/out_a.webp", error=None, preserved=None,
         flags=(), quality_flags=(), tags=(), raters=(), notes=()):
    return {"run": run, "repeat": repeat, "mode": mode, "result": result, "error": error, "preserved": preserved,
            "flags": list(flags), "quality_flags": list(quality_flags), "tags": list(tags),
            "raters": list(raters), "notes": list(notes)}


def test_page_escapes_everything(board_mod):
    items = [_item(file=XSS + ".webp", entry={"file": XSS + ".webp", "item": XSS, "note": XSS},
                   orig=("r1", XSS + ".webp"),
                   results=[_res(run="r1", mode=XSS, preserved=False, flags=[XSS], quality_flags=[XSS],
                                 tags=[XSS], raters=[XSS], notes=[f"kim: {XSS}"]),
                            _res(run=XSS, repeat=2, error=XSS, result=None)])]
    h = board_mod.page(items, "tok")
    body = h.split("<script>\nconst TOKEN")[0]
    assert "<script>alert" not in body
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    assert 'data-file="&lt;script&gt;alert(1)&lt;/script&gt;.webp"' in body
    assert h.count("<script>") == 1                                 # 페이지 자체 스크립트 하나뿐


def test_page_escapes_attribute_quotes(board_mod):
    f = 'x" onmouseover="alert(1).webp'
    h = board_mod.page([_item(file=f, entry={"file": f})], "tok")
    assert 'onmouseover="alert' not in h
    assert 'data-file="x&quot; onmouseover=&quot;alert(1).webp"' in h


def test_page_error_truncated_and_no_img(board_mod):
    h = board_mod.page([_item(orig=None, results=[_res(error="E" * 200, result="files/out_a.webp")])], "tok")
    assert "E" * 80 in h and "E" * 81 not in h
    assert "<img" not in h                                          # 오류면 결과 이미지를 안 건다
    assert "원본 없음" in h


def test_page_missing_result_shows_placeholder(board_mod):
    h = board_mod.page([_item(results=[_res(result=None)])], "tok")
    assert "이미지 없음" in h and 'src="/r1/files/orig_a.webp"' in h


def test_page_unsafe_paths_not_linked(board_mod):
    h = board_mod.page([_item(orig=("r1", "../dataset.json"), results=[_res(result="/etc/passwd")])], "tok")
    assert "../dataset.json" not in h and "/etc/passwd" not in h and "<img" not in h
    assert "이미지 없음" in h and "원본 없음" in h


def test_page_flags_and_quality_lines(board_mod):
    h = board_mod.page([_item(results=[_res(preserved=False, flags=["text_changed"], tags=["logo"],
                                            quality_flags=["background_issue"], raters=["kim"],
                                            notes=["kim: 메모"])])], "tok")
    assert '<div class="small">text_changed, logo</div>' in h
    assert "배경·구도: background_issue" in h
    assert "채점 kim" in h and "kim: 메모" in h


def test_page_no_quality_line_when_empty(board_mod):
    assert "배경·구도:" not in board_mod.page([_item(results=[_res(preserved=True)])], "tok")


def test_page_checkbox_state_and_counts(board_mod):
    items = [_item("a.webp", entry={"file": "a.webp", "set": "failure"},
                   results=[_res(preserved=False), _res(repeat=2, preserved=True), _res(repeat=3)]),
             _item("b.webp", entry={"file": "b.webp", "set": "dev"}, results=[_res(preserved=True)]),
             _item("c.webp", entry={}, orig=None)]
    h = board_mod.page(items, "tok")
    assert '<input type="checkbox" data-file="a.webp" checked>' in h
    assert '<input type="checkbox" data-file="b.webp">' in h
    assert '<input type="checkbox" data-file="c.webp">' in h
    assert '보존 실패 1 / 채점 2 · 결과 3개' in h
    assert 'class="item fail" data-fails="1"' in h and 'class="item" data-fails="0"' in h
    assert "보존 ✗" in h and "보존 ✓" in h and "채점 전" in h


def test_page_token_embedded_as_json(board_mod):
    h = board_mod.page([], "abc-DEF_123")
    assert 'const TOKEN = "abc-DEF_123";' in h
    assert "'X-Board-Token': TOKEN" in h


def test_page_tabs_and_current_run_note(board_mod, tmp_path):
    (tmp_path / "results" / "runs" / "r 1").mkdir(parents=True)
    (tmp_path / "results" / "runs" / "r 1" / "meta.json").write_text(json.dumps({"note": "<b>바꾼 것</b>", "repeat": 2,
                                                                     "composition": "auto"}))
    h = board_mod.page([_item(results=[_res(run="r 1")])], "tok", runs=["r2", "r 1"], current="r 1")
    assert '<a class="tab" href="/?run=r2">r2</a>' in h
    assert '<a class="tab on" href="/?run=r%201">r 1</a>' in h
    assert "&lt;b&gt;바꾼 것&lt;/b&gt;" in h and "<b>바꾼 것</b>" not in h
    assert "repeat 2" in h and "구도 auto" in h and '<a href="/r%201/review.html">' in h


def test_page_without_runs_has_no_tabs_or_note(board_mod):
    h = board_mod.page([], "tok")
    assert 'class="tab"' not in h and 'class="tab on"' not in h and "메모 없음" not in h


def _run(tmp_path, run_id, created, rows, files=True):
    d = tmp_path / "results" / "runs" / run_id
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"created": created, "note": run_id}))
    (d / "results.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    if files:
        (d / "files").mkdir()


def test_viewable_runs_newest_first_and_skips_pruned_or_analyze_only(board_mod, tmp_path):
    ok = [{"file": "a.webp", "repeat": 1, "result": "files/a.jpg"}]
    _run(tmp_path, "zzz-old", "2026-10-01T09:00:00", ok)
    _run(tmp_path, "aaa-new", "2026-10-03T09:00:00", ok)
    _run(tmp_path, "pruned", "2026-10-04T09:00:00", ok, files=False)          # 이미지 지운 실행
    _run(tmp_path, "analyze", "2026-10-05T09:00:00", [{"file": "a.webp", "repeat": 1, "photo_type": "product"}])
    assert board_mod.viewable_runs() == ["aaa-new", "zzz-old"]


def test_collect_only_run_and_without_unrun_photos(board_mod, tmp_path):
    write_dataset(board_mod, [{"file": "a.webp"}, {"file": "b.webp"}])
    _run(tmp_path, "r1", "2026-10-03T09:00:00", [{"file": "a.webp", "repeat": 1, "result": "files/a.jpg",
                                                  "composition": "album_front"}])
    _run(tmp_path, "r2", "2026-10-03T10:00:00", [{"file": "b.webp", "repeat": 1, "result": "files/b.jpg",
                                                  "composition_skipped": "각도 side 는 아님"}])
    items = board_mod.collect("r1", include_unrun=False)
    assert [it["file"] for it in items] == ["a.webp"] and items[0]["results"][0]["composition"] == "album_front"
    assert {it["file"] for it in board_mod.collect()} == {"a.webp", "b.webp"}
    h = board_mod.page(board_mod.collect("r2", include_unrun=False), "tok")
    assert "각도 side 는 아님" in h


# ── 서버 ────────────────────────────────────────
TOKEN = "test-token"


@pytest.fixture
def server(board_mod):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), board_mod.make_handler(TOKEN))
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv.server_address[1]
    srv.shutdown()
    srv.server_close()
    t.join(timeout=5)


def req(port, method, path, body=None, headers=None):
    """http.client 는 Host 를 '127.0.0.1:<port>' 로 넣는다 (headers 에 Host 를 주면 그걸 쓴다)."""
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, r.read(), dict(r.getheaders())
    finally:
        c.close()


def post(port, payload, token=TOKEN, ctype="application/json", raw=None, headers=None):
    h = {"Content-Type": ctype} if ctype is not None else {}
    if token is not None:
        h["X-Board-Token"] = token
    h.update(headers or {})
    body = raw if raw is not None else json.dumps(payload).encode()
    return req(port, "POST", "/api/failure", body=body, headers=h)[:2]


def test_server_get_index(board_mod, server, tmp_path):
    write_dataset(board_mod, [{"file": "a.webp", "set": "failure"}, {"file": "b.webp"}])
    _run(tmp_path, "old", "2026-10-01T09:00:00", [{"file": "b.webp", "repeat": 1, "result": "files/b.jpg"}])
    _run(tmp_path, "new", "2026-10-03T09:00:00", [{"file": "a.webp", "repeat": 1, "result": "files/a.jpg"}])
    for path in ("/", "/index.html", "/?x=1", "/?run=nope"):          # 기본 · 모르는 실행 = 최신
        st, body, hd = req(server, "GET", path)
        assert st == 200 and hd["Content-Type"] == "text/html; charset=utf-8" and hd["Cache-Control"] == "no-store"
        h = body.decode()
        assert "eval 결과판" in h and 'data-file="a.webp" checked' in h and json.dumps(TOKEN) in h
        assert 'data-file="b.webp"' not in h                           # 다른 실행 · 안 돌린 사진은 안 나온다
    h = req(server, "GET", "/?run=old")[1].decode()
    assert 'data-file="b.webp"' in h and 'data-file="a.webp"' not in h and 'class="tab on" href="/?run=old"' in h


def test_server_get_index_without_runs(board_mod, server):
    write_dataset(board_mod, [{"file": "a.webp"}])
    st, body, _ = req(server, "GET", "/")
    assert st == 200 and 'data-file="a.webp"' not in body.decode()


def test_server_head_index_has_no_body(server):
    st, body, hd = req(server, "HEAD", "/")
    assert st == 200 and body == b"" and int(hd["Content-Length"]) > 0


@pytest.mark.parametrize("path,ctype,data", [
    ("/compare-r1-r2.html", "text/html; charset=utf-8", b"cmp"),
    ("/r1/review.html", "text/html; charset=utf-8", b"DATA review.html"),
    ("/r1/report.md", "text/plain; charset=utf-8", b"DATA report.md"),
    ("/r1/files/a.webp", "image/webp", b"DATA files/a.webp"),
    ("/r1/files/b.JPG", "image/jpeg", b"DATA files/b.JPG"),
    ("/r1/files/sub/c.png?v=1", "image/png", b"DATA files/sub/c.png"),
])
def test_server_serves_allowed_static_files(server, run_files, path, ctype, data):
    st, body, hd = req(server, "GET", path)
    assert (st, body, hd["Content-Type"]) == (200, data, ctype)
    st, body, hd = req(server, "HEAD", path)
    assert st == 200 and body == b"" and hd["Content-Length"] == str(len(data))


@pytest.mark.parametrize("path", ["/r1/", "/r1/files/", "/r1/results.jsonl", "/r1/carret.db", "/r1/storage/x.webp",
                                  "/../dataset.json", "/r1/../../dataset.json", "/%2e%2e/dataset.json",
                                  "/..%2fdataset.json", "/r1/files/../results.jsonl", "/none.html"])
def test_server_blocks_other_paths(board_mod, server, run_files, path):
    write_dataset(board_mod, [{"file": "secret.webp"}])
    st, body, _ = req(server, "GET", path)
    assert st == 404 and body == b"not found"
    assert req(server, "HEAD", path)[0] == 404


def test_server_blocks_symlink_outside(server, run_files, tmp_path):
    (run_files / "r1" / "files" / "evil.webp").symlink_to(tmp_path / "outside" / "secret.webp")
    st, body, _ = req(server, "GET", "/r1/files/evil.webp")
    assert st == 404 and b"SECRET" not in body


@pytest.mark.parametrize("host", ["localhost", "localhost:8766", "127.0.0.1", "LOCALHOST:1",
                                  "abc-8766.app.github.dev", "ABC-8766.APP.GITHUB.DEV:443"])
def test_server_allows_local_hosts(server, host):
    assert req(server, "GET", "/", headers={"Host": host})[0] == 200


@pytest.mark.parametrize("host", ["evil.com", "evil.com:8766", "127.0.0.1.evil.com", "localhost.evil.com",
                                  "app.github.dev.evil.com", "[::1]:8766", "0.0.0.0", ""])
def test_server_rejects_other_hosts(board_mod, server, run_files, host):
    write_dataset(board_mod, [{"file": "a.webp"}])
    for method, path in (("GET", "/"), ("GET", "/r1/review.html"), ("HEAD", "/")):
        st, body, _ = req(server, method, path, headers={"Host": host})
        assert st == 403, (method, path)
        assert b"test-token" not in body
    st, _ = post(server, {"file": "a.webp", "on": True}, headers={"Host": host})
    assert st == 403
    assert read_dataset(board_mod) == [{"file": "a.webp"}]


def test_server_rejects_missing_host_header(server):
    c = http.client.HTTPConnection("127.0.0.1", server, timeout=5)
    try:
        c.putrequest("GET", "/", skip_host=True)
        c.endheaders()
        assert c.getresponse().status == 403
    finally:
        c.close()


def test_server_post_wrong_path_404(server):
    st, _, _ = req(server, "POST", "/api/other", body=b"{}",
                   headers={"Content-Type": "application/json", "X-Board-Token": TOKEN})
    assert st == 404


@pytest.mark.parametrize("token", [None, "", "wrong", TOKEN + "x"])
def test_server_post_bad_token_403(board_mod, server, token):
    write_dataset(board_mod, [{"file": "a.webp"}])
    st, _ = post(server, {"file": "a.webp", "on": True}, token=token)
    assert st == 403
    assert read_dataset(board_mod) == [{"file": "a.webp"}]


@pytest.mark.parametrize("ctype", [None, "text/plain", "application/x-www-form-urlencoded", "multipart/form-data"])
def test_server_post_bad_content_type_403(board_mod, server, ctype):
    write_dataset(board_mod, [{"file": "a.webp"}])
    st, _ = post(server, {"file": "a.webp", "on": True}, ctype=ctype)
    assert st == 403
    assert read_dataset(board_mod) == [{"file": "a.webp"}]


@pytest.mark.parametrize("raw", [b"not json", b"\xff\xfe", b"[1, 2]", b"null", b"\"a.webp\"", b"{}",
                                 b'{"file": "a.webp"}', b'{"on": true}', b'{"file": 1, "on": true}',
                                 b'{"file": "a.webp", "on": "true"}', b'{"file": "a.webp", "on": 1}',
                                 b'{"file": null, "on": false}'])
def test_server_post_bad_body_400(board_mod, server, raw):
    write_dataset(board_mod, [{"file": "a.webp"}])
    st, _ = post(server, None, raw=raw)
    assert st == 400
    assert read_dataset(board_mod) == [{"file": "a.webp"}]


def test_server_post_empty_body_400(server):
    assert post(server, None, raw=b"")[0] == 400


def test_server_post_too_large_400(board_mod, server):
    write_dataset(board_mod, [{"file": "a.webp"}])
    raw = json.dumps({"file": "a.webp", "on": True, "pad": "x" * board_mod.MAX_BODY}).encode()
    assert len(raw) > board_mod.MAX_BODY
    assert post(server, None, raw=raw)[0] == 400
    assert read_dataset(board_mod) == [{"file": "a.webp"}]


def test_server_post_body_at_max_is_accepted(board_mod, server):
    write_dataset(board_mod, [{"file": "a.webp"}])
    base = json.dumps({"file": "a.webp", "on": True, "pad": ""})
    raw = json.dumps({"file": "a.webp", "on": True, "pad": "x" * (board_mod.MAX_BODY - len(base))}).encode()
    assert len(raw) == board_mod.MAX_BODY
    assert post(server, None, raw=raw)[0] == 200


@pytest.mark.parametrize("cl", ["abc", "-1"])
def test_server_post_bad_content_length_400(server, cl):
    c = http.client.HTTPConnection("127.0.0.1", server, timeout=5)
    try:
        c.putrequest("POST", "/api/failure")
        c.putheader("Content-Type", "application/json")
        c.putheader("X-Board-Token", TOKEN)
        c.putheader("Content-Length", cl)
        c.endheaders()
        assert c.getresponse().status == 400
    finally:
        c.close()


def test_server_post_unknown_file_404(board_mod, server):
    write_dataset(board_mod, [{"file": "a.webp"}])
    st, body = post(server, {"file": "zzz.webp", "on": True})
    assert st == 404 and "dataset.json 에 없는 사진" in body.decode()
    assert read_dataset(board_mod) == [{"file": "a.webp"}]


def test_server_post_ok_on_then_off(board_mod, server):
    write_dataset(board_mod, [{"file": "a.webp", "note": "근거"}, {"file": "b.webp"}])
    st, body = post(server, {"file": "a.webp", "on": True}, ctype="application/json; charset=utf-8")
    assert (st, body) == (200, b"ok")
    assert read_dataset(board_mod)[0] == {"file": "a.webp", "note": "근거", "set": "failure",
                                          "failure_added": date.today().isoformat()}
    assert req(server, "GET", "/")[0] == 200
    assert post(server, {"file": "a.webp", "on": False})[0] == 200
    assert read_dataset(board_mod) == [{"file": "a.webp", "note": "근거"}, {"file": "b.webp"}]


def test_server_post_unicode_file(board_mod, server):
    write_dataset(board_mod, [{"file": "신발.webp"}])
    assert post(server, {"file": "신발.webp", "on": True})[0] == 200
    assert read_dataset(board_mod)[0]["set"] == "failure"
