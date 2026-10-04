"""eval/board.py — 결과판 (메모·결과 읽기 · 사진별 모으기/정렬 · 실패 모음 저장 · 파일 열기 · HTML · 서버)."""
import contextlib
import csv
import http.client
import json
import threading
from datetime import date
from http.server import ThreadingHTTPServer

import pytest


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


# ── _notes ──────────────────────────────────────
def test_notes_collects_per_rater_skips_template_and_empty(board_mod, tmp_path):
    write_review(tmp_path, "r1", "kim", [ok("a.webp", note="배경 얼룩"), ok("b.webp", note="  ")])
    write_review(tmp_path, "r1", "lee", [bad("a.webp", note="로고 바뀜")])
    write_review(tmp_path, "r1", "TEMPLATE", [ok("a.webp", note="템플릿 예시")])
    n = board_mod._notes("r1")
    assert n[("a.webp", 1)] == ["kim: 배경 얼룩", "lee: 로고 바뀜"]
    assert ("b.webp", 1) not in n                          # 공백만 있는 메모는 없음


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


def test_collect_includes_unrun_dataset_photos_and_unknown_files(board_mod, tmp_path):
    write_dataset(board_mod, [{"file": "never.webp", "set": "failure"}])
    write_run(tmp_path, "r1", [R("ghost.webp")])
    items = {it["file"]: it for it in board_mod.collect()}
    assert items["never.webp"] == {"file": "never.webp", "entry": {"file": "never.webp", "set": "failure"},
                                   "orig": None, "results": []}
    assert items["ghost.webp"]["entry"] == {} and len(items["ghost.webp"]["results"]) == 1


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


def test_set_failure_off_removes_only_set_and_date(board_mod, lock_calls):
    write_dataset(board_mod, [{"file": "a.webp", "set": "failure", "failure_added": "2026-01-01",
                               "failure_tags": ["logo"], "note": "근거 메모", "split": "test"}])
    assert board_mod.set_failure("a.webp", False) is True
    assert read_dataset(board_mod) == [{"file": "a.webp", "failure_tags": ["logo"], "note": "근거 메모",
                                        "split": "test"}]
    assert lock_calls == ["lock", "save-in-lock"]


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


def test_image_symlink_pointing_outside_is_blocked(board_mod, run_files, tmp_path):
    (run_files / "r1" / "files" / "link.webp").symlink_to(tmp_path / "outside" / "secret.webp")
    assert board_mod._image("r1", "files/link.webp") is None


@pytest.mark.parametrize("path", [
    "/", "/r1", "/r1/", "/r1/files", "/r1/files/", "/r1/files/sub",])
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
@pytest.mark.parametrize("rel", ["", None, "/etc/passwd",])
def test_src_blocks_absolute_and_parent(board_mod, rel):
    assert board_mod._src("r1", rel) is None


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


@pytest.mark.parametrize("path", ["/r1/", "/r1/files/", "/r1/results.jsonl", "/r1/carret.db",])
def test_server_blocks_other_paths(board_mod, server, run_files, path):
    write_dataset(board_mod, [{"file": "secret.webp"}])
    st, body, _ = req(server, "GET", path)
    assert st == 404 and body == b"not found"
    assert req(server, "HEAD", path)[0] == 404


@pytest.mark.parametrize("host", ["evil.com", "evil.com:8766", "127.0.0.1.evil.com",])
def test_server_rejects_other_hosts(board_mod, server, run_files, host):
    write_dataset(board_mod, [{"file": "a.webp"}])
    for method, path in (("GET", "/"), ("GET", "/r1/review.html"), ("HEAD", "/")):
        st, body, _ = req(server, method, path, headers={"Host": host})
        assert st == 403, (method, path)
        assert b"test-token" not in body
    st, _ = post(server, {"file": "a.webp", "on": True}, headers={"Host": host})
    assert st == 403
    assert read_dataset(board_mod) == [{"file": "a.webp"}]


@pytest.mark.parametrize("token", [None, "", "wrong",])
def test_server_post_bad_token_403(board_mod, server, token):
    write_dataset(board_mod, [{"file": "a.webp"}])
    st, _ = post(server, {"file": "a.webp", "on": True}, token=token)
    assert st == 403
    assert read_dataset(board_mod) == [{"file": "a.webp"}]


@pytest.mark.parametrize("ctype", [None, "text/plain", "application/x-www-form-urlencoded",])
def test_server_post_bad_content_type_403(board_mod, server, ctype):
    write_dataset(board_mod, [{"file": "a.webp"}])
    st, _ = post(server, {"file": "a.webp", "on": True}, ctype=ctype)
    assert st == 403
    assert read_dataset(board_mod) == [{"file": "a.webp"}]


def test_server_post_too_large_400(board_mod, server):
    write_dataset(board_mod, [{"file": "a.webp"}])
    raw = json.dumps({"file": "a.webp", "on": True, "pad": "x" * board_mod.MAX_BODY}).encode()
    assert len(raw) > board_mod.MAX_BODY
    assert post(server, None, raw=raw)[0] == 400
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


def test_collect_extra_with_results_still_shown(board_mod, tmp_path):
    """--only 로 돌린 추가 사진은 결과가 있으니 보인다 — 안 돌린 것만 뺀다."""
    entry = {"file": "a_p02.jpg", "post": "a", "post_index": 2}
    write_dataset(board_mod, [entry, {"file": "a_p03.jpg", "post": "a", "post_index": 3}])
    write_run(tmp_path, "r1", [R("a_p02.jpg")])
    [it] = board_mod.collect()
    assert it["file"] == "a_p02.jpg" and it["entry"] == entry and len(it["results"]) == 1


