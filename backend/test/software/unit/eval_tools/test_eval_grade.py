"""eval/grade.py — order · apply · read/write_sheet · load_rows · page · make_handler(실제 HTTP 서버) · report 호환.

grade_mod 픽스처(conftest)가 HERE 를 tmp_path 로 돌려 둔다 — 실제 runs/·reviews/ 는 건드리지 않는다.
"""
import csv
import http.client
import json
import re
import threading
from http.server import ThreadingHTTPServer

import pytest

RUN_ID = "fake-run"
NAME = "tester"


def _rows(nfiles: int, repeats: int, mode: str = "composite", prefix: str = "f") -> list[dict]:
    return [{"file": f"{prefix}{i}.webp", "repeat": r, "mode": mode,
             "orig": f"files/{prefix}{i}__r{r}_orig.webp", "result": f"files/{prefix}{i}__r{r}_result.jpg"}
            for i in range(nfiles) for r in range(1, repeats + 1)]


def _keys(rows):
    return [(r["file"], int(r["repeat"])) for r in rows]


# ── order ──────────────────────────────────────
def test_order_same_name_same_order(grade_mod):
    rows = _rows(10, 3)
    assert _keys(grade_mod.order(rows, "cha0620")) == _keys(grade_mod.order(rows, "cha0620"))


@pytest.mark.parametrize("nfiles,repeats", [(1, 3), (3, 2),])
def test_order_keeps_same_photo_adjacent(grade_mod, nfiles, repeats):
    """page() 가 붙어 있는 줄을 카드 한 장으로 묶는다 — 같은 사진은 한 덩어리로만 나온다."""
    rows = _rows(nfiles, repeats)
    for k in range(20):
        files = [r["file"] for r in grade_mod.order(rows, f"n{k}")]
        runs = [f for i, f in enumerate(files) if i == 0 or files[i - 1] != f]
        assert len(runs) == len(set(runs)) == nfiles, f"n{k}"


# ── apply ──────────────────────────────────────
def test_apply_flags_and_reviewed(grade_mod):
    sheet = {}
    row = grade_mod.apply(sheet, ("a.webp", 1), {"text_changed": True, "framing_issue": 1,
                                                 "wear_changed": False, "reviewed": True})
    assert row["text_changed"] == "1" and row["framing_issue"] == "1"
    assert row["wear_changed"] == "" and row["shape_color_changed"] == "" and row["background_issue"] == ""
    assert row["reviewed"] == "y"
    assert sheet[("a.webp", 1)] is row


def test_apply_unchecking_clears_previous_flag_keeps_other_cols(grade_mod):
    sheet = {("a.webp", 1): {"file": "a.webp", "repeat": "1", "mode": "generate", "text_changed": "1",
                              "reviewed": "y", "note": "old"}}
    row = grade_mod.apply(sheet, ("a.webp", 1), {"reviewed": True, "note": None})
    assert row["text_changed"] == "" and row["note"] == ""
    assert row["mode"] == "generate" and row["file"] == "a.webp"


# ── write_sheet ────────────────────────────────
def _no_tmp(d):
    return not [p for p in d.iterdir() if p.name.endswith(".tmp")]


def test_write_sheet_preserves_existing_and_roundtrips_comma_note(grade_mod, tmp_path):
    rows = _rows(2, 2)
    p = tmp_path / "r.csv"
    sheet = {}
    grade_mod.apply(sheet, ("f1.webp", 2), {"reviewed": True, "text_changed": True,
                                            "note": '쉼표, "따옴표"\n줄바꿈'})
    grade_mod.write_sheet(p, rows, sheet)
    back = grade_mod.read_sheet(p)
    assert len(back) == 4
    assert back[("f1.webp", 2)]["note"] == '쉼표, "따옴표" 줄바꿈'
    assert back[("f1.webp", 2)]["text_changed"] == "1" and back[("f1.webp", 2)]["reviewed"] == "y"
    grade_mod.apply(back, ("f0.webp", 1), {"reviewed": True})
    grade_mod.write_sheet(p, rows, back)
    again = grade_mod.read_sheet(p)
    assert again[("f1.webp", 2)]["note"] == '쉼표, "따옴표" 줄바꿈'
    assert again[("f0.webp", 1)]["reviewed"] == "y"
    assert _no_tmp(tmp_path)


def test_write_sheet_failure_leaves_original_and_no_tmp(grade_mod, tmp_path, monkeypatch):
    p = tmp_path / "r.csv"
    grade_mod.write_sheet(p, [{"file": "a.webp", "repeat": 1}], {("a.webp", 1): {"reviewed": "y"}})
    before = p.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(grade_mod.os, "replace", boom)
    with pytest.raises(OSError):
        grade_mod.write_sheet(p, [{"file": "a.webp", "repeat": 1}], {})
    assert p.read_bytes() == before
    assert _no_tmp(tmp_path)


# ── 가짜 run ───────────────────────────────────
@pytest.fixture
def fake_run(grade_mod, tmp_path):
    """층 코드 이름(none_/doc_/edge_)과 __r 가 든 실제 같은 파일 이름 — HTML 에 새면 안 된다."""
    run_dir = tmp_path / "results" / "runs" / RUN_ID
    (run_dir / "files").mkdir(parents=True)
    results = []
    for stem in ("none_mug", "doc_album", "edge_shoe"):
        for r in (1, 2):
            results.append({"file": f"{stem}.webp", "repeat": r, "mode": "composite",
                            "orig": f"files/{stem}__r{r}_orig.webp", "result": f"files/{stem}__r{r}_result.jpg"})
    results.append({"file": "err_x.webp", "repeat": 1, "error": "boom"})                    # 실패
    results.append({"file": "edge_only.webp", "repeat": 1, "mode": None})                   # analyze 만
    results.append({"file": "edge_half.webp", "repeat": 1, "orig": "files/x.webp", "result": None})
    with open(run_dir / "results.jsonl", "w", encoding="utf-8") as f:
        for r in results:
            f.write(json.dumps(r) + "\n")
            if r.get("orig") and r.get("result"):
                (run_dir / r["orig"]).write_bytes(f"ORIG {r['file']} {r['repeat']}".encode())
                (run_dir / r["result"]).write_bytes(f"RES {r['file']} {r['repeat']}".encode())
    (run_dir / "secret.txt").write_text("secret", encoding="utf-8")
    return run_dir


# ── page ───────────────────────────────────────
def test_page_hides_file_names_and_escapes(grade_mod, fake_run):
    rows = grade_mod.load_rows(RUN_ID)
    items = grade_mod.order(rows, NAME)
    gt = {"doc_album.webp": {"key_texts": ["<LOGO>", 2024], "note": "라벨 & 메모"}}
    text = grade_mod.page(RUN_ID, NAME, items, {}, gt, "abc123")
    assert text.count('<section class="card') == 6
    assert [int(x) for x in re.findall(r'data-id="(\d+)"', text)] == list(range(1, 7))
    # 사진당 카드 한 장 — 원본은 사진마다 한 번, 결과는 A · B
    assert text.count('<article class="photo">') == 3
    assert re.findall(r'src="/img/(\d+)/orig\?v=', text) == ["1", "3", "5"]
    # 순서가 바뀌면 이미지 주소도 바뀐다 — 옛 순서로 캐시된 사진이 다른 카드에 보이지 않게
    ver = set(re.findall(r'\?v=(\w+)"', text))
    other = grade_mod.page(RUN_ID, NAME, grade_mod.order(rows, "someone-else"), {}, gt, "abc123")
    assert len(ver) == 1 and ver.isdisjoint(re.findall(r'\?v=(\w+)"', other))
    assert text.count('alt="결과 A"') == text.count('alt="결과 B"') == 3
    for leak in ("none_", "doc_", "edge_", "__r", ".webp", "files/", "composite"):
        assert leak not in text, leak
    assert "&lt;LOGO&gt;, 2024" in text and "라벨 &amp; 메모" in text
    assert 'const TOKEN="abc123";' in text


# ── HTTP 서버 ──────────────────────────────────
@pytest.fixture
def server(grade_mod, fake_run, tmp_path):
    rows = grade_mod.load_rows(RUN_ID)
    gt = {"doc_album.webp": {"key_texts": ["LOGO"]}}
    sheet_path = tmp_path / "results" / "reviews" / RUN_ID / f"{NAME}.csv"
    sheet_path.parent.mkdir(parents=True)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), grade_mod.make_handler(RUN_ID, NAME, rows, gt, sheet_path))
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    port = srv.server_address[1]
    status, _, body = _req(port, "GET", "/")
    assert status == 200
    token = re.search(r'const TOKEN="([0-9a-f]+)";', body.decode()).group(1)
    yield {"port": port, "sheet": sheet_path, "rows": rows, "token": token,
           "items": grade_mod.order(rows, NAME), "html": body.decode()}
    srv.shutdown()
    srv.server_close()


def _req(port, method, path, body=None, headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    c.request(method, path, body=body, headers=headers or {})
    r = c.getresponse()
    data = r.read()
    c.close()
    return r.status, r.getheader("Content-Type"), data


def _save(s, payload, token=None, ctype="application/json"):
    body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    headers = {"Content-Type": ctype} if ctype else {}
    if token is not False:
        headers["X-Grade-Token"] = s["token"] if token is None else token
    return _req(s["port"], "POST", "/api/save", body, headers)


@pytest.mark.parametrize("path", [
    "/img/0/orig", "/img/7/orig", "/img/-1/orig", "/img/1/other", "/img/1/orig/x",])
def test_get_bad_paths_404(server, path):
    assert _req(server["port"], "GET", path)[0] == 404


def test_get_img_path_escape_in_results_404(grade_mod, fake_run, tmp_path):
    """results.jsonl 의 orig/result 가 run/files 밖을 가리켜도 내주지 않는다."""
    rows = [{"file": "a.webp", "repeat": 1, "orig": "files/../secret.txt", "result": "/etc/passwd"},
            {"file": "b.webp", "repeat": 1, "orig": "../../../etc/hostname", "result": "files/missing.jpg"}]
    srv = ThreadingHTTPServer(("127.0.0.1", 0),
                              grade_mod.make_handler(RUN_ID, NAME, rows, {}, tmp_path / "x.csv"))
    threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        for i in (1, 2):
            for kind in ("orig", "result"):
                assert _req(srv.server_address[1], "GET", f"/img/{i}/{kind}")[0] == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_post_save_writes_csv_by_id(server, grade_mod):
    items, sheet_path = server["items"], server["sheet"]
    status, _, body = _save(server, {"id": 2, "reviewed": True, "text_changed": True,
                                     "note": "쉼표, 메모\n둘째 줄"})
    assert status == 200 and body == b"ok"
    sheet = grade_mod.read_sheet(sheet_path)
    assert len(sheet) == len(server["rows"])
    key = (items[1]["file"], int(items[1]["repeat"]))
    assert sheet[key]["reviewed"] == "y" and sheet[key]["text_changed"] == "1"
    assert sheet[key]["note"] == "쉼표, 메모 둘째 줄" and sheet[key]["mode"] == "composite"
    # CSV 는 results 순서 (화면 순서 아님)
    assert list(sheet) == _keys(server["rows"])
    # id 가 문자열이어도 받는다 · 앞서 저장한 값은 남는다
    assert _save(server, {"id": "1", "reviewed": True})[0] == 200
    sheet = grade_mod.read_sheet(sheet_path)
    assert sheet[(items[0]["file"], int(items[0]["repeat"]))]["reviewed"] == "y"
    assert sheet[key]["text_changed"] == "1"
    assert _no_tmp(sheet_path.parent)
    html = _req(server["port"], "GET", "/")[2].decode()
    assert html.count('class="card done"') == 2
    assert 'class="card done" data-id="1"' in html and 'class="card done" data-id="2"' in html


@pytest.mark.parametrize("token,ctype", [
    (False, "application/json"),          # 토큰 없음
    ("deadbeef", "application/json"),     # 틀린 토큰
    ("", "application/json"),])
def test_post_save_requires_token_and_json_403(server, token, ctype):
    status, _, _ = _save(server, {"id": 1, "reviewed": True}, token=token, ctype=ctype)
    assert status == 403
    assert not server["sheet"].exists()


@pytest.mark.parametrize("payload", [
    {"id": 0, "reviewed": True},
    {"id": 7, "reviewed": True},
    {"id": -1, "reviewed": True},])
def test_post_save_bad_body_400(server, payload):
    status, _, _ = _save(server, payload)
    assert status == 400
    assert not server["sheet"].exists()


def test_concurrent_saves_keep_all_rows(server, grade_mod):
    n = len(server["items"])
    ts = [threading.Thread(target=_save, args=(server, {"id": i, "reviewed": True})) for i in range(1, n + 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    sheet = grade_mod.read_sheet(server["sheet"])
    assert len(sheet) == n and all(r["reviewed"] == "y" for r in sheet.values())


# ── report.py 호환 ─────────────────────────────
def test_saved_csv_readable_by_report_load_reviews(server, report_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    items = server["items"]
    assert _save(server, {"id": 1, "reviewed": True})[0] == 200
    assert _save(server, {"id": 2, "reviewed": True, "text_changed": True, "note": "a, b"})[0] == 200
    assert _save(server, {"id": 3, "reviewed": True, "background_issue": True})[0] == 200
    assert _save(server, {"id": 4, "text_changed": True})[0] == 200          # reviewed 없음 → 집계 제외
    reviews = report_mod.load_reviews(RUN_ID)
    assert set(reviews) == {NAME}
    got = reviews[NAME]
    k = [(it["file"], int(it["repeat"])) for it in items]
    assert set(got) == {k[0], k[1], k[2]}
    assert got[k[0]] == {"preserved": True, "clean": True, "flags": [], "tags": [], "quality": None}
    assert got[k[1]]["preserved"] is False and got[k[1]]["flags"] == ["text_changed"]
    assert got[k[2]]["preserved"] is True and got[k[2]]["clean"] is False


def test_apply_failure_tags_drops_unknown_and_non_strings(grade_mod):
    data = {"failure_tags": ["color_changed", "unknown", "", " detail_lost", "DETAIL_LOST", "detail_lost;x",
                             None, 1, ["detail_lost"], {"t": 1}, "<script>", "color_changed", "tag_lost"]}
    row = grade_mod.apply({}, ("a.webp", 1), data)
    assert row["failure_tags"] == "color_changed;tag_lost"     # 중복은 한 번, 공백·대소문자 다른 건 버림


def test_write_sheet_old_sheet_without_failure_tags_column(grade_mod, tmp_path):
    """옛 채점표(failure_tags 칸 없음)도 읽고, 다시 쓸 때 빈 칸으로 채운다."""
    path = tmp_path / "old.csv"
    old_cols = [c for c in grade_mod.COLS if c not in ("failure_tags",)]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=old_cols)
        w.writeheader()
        w.writerow({"file": "f0.webp", "repeat": "1", "reviewed": "y", "text_changed": "1"})
    sheet = grade_mod.read_sheet(path)
    grade_mod.write_sheet(path, _rows(1, 1), sheet)
    back = grade_mod.read_sheet(path)[("f0.webp", 1)]
    assert back["failure_tags"] == "" and back["text_changed"] == "1" and back["reviewed"] == "y"


def test_post_save_writes_failure_tags_and_report_reads_them(server, grade_mod, report_mod, tmp_path, monkeypatch):
    it = server["items"][0]
    key = (it["file"], int(it["repeat"]))
    payload = {"id": 1, "reviewed": True, "shape_color_changed": True,
               "failure_tags": ["wear_removed", "bogus", 3, "color_changed"]}
    assert _save(server, payload)[0] == 200
    row = grade_mod.read_sheet(server["sheet"])[key]
    assert row["failure_tags"] == "color_changed;wear_removed"
    # 태그가 문자열이면(리스트 아님) 버린다 — 400 이 아니라 저장은 된다
    assert _save(server, {"id": 1, "reviewed": True, "failure_tags": "color_changed"})[0] == 200
    assert grade_mod.read_sheet(server["sheet"])[key]["failure_tags"] == ""
    assert _save(server, {"id": 1, "reviewed": True, "failure_tags": ["tag_lost"]})[0] == 200
    # 다시 열면 체크돼 있다
    html = _req(server["port"], "GET", "/")[2].decode()
    assert html.count('data-tag="tag_lost" checked') == 1 and html.count("<details open>") == 1
    # report 가 같은 파일을 읽는다
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    got = report_mod.load_reviews(RUN_ID)[NAME][key]
    assert got["tags"] == ["tag_lost"]
