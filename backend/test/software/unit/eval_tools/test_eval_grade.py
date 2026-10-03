"""eval/grade.py — order · apply · read/write_sheet · load_rows · page · make_handler(실제 HTTP 서버) · report 호환.

grade_mod 픽스처(conftest)가 HERE 를 tmp_path 로 돌려 둔다 — 실제 runs/·reviews/ 는 건드리지 않는다.
"""
import csv
import http.client
import json
import re
import socket
import threading
from collections import Counter
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


def test_order_different_names_usually_differ(grade_mod):
    rows = _rows(10, 3)
    orders = {tuple(_keys(grade_mod.order(rows, f"rater{k}"))) for k in range(10)}
    assert len(orders) >= 9


def test_order_does_not_mutate_input(grade_mod):
    rows = _rows(5, 2)
    before = _keys(rows)
    grade_mod.order(rows, "x")
    assert _keys(rows) == before


@pytest.mark.parametrize("nfiles,repeats", [(0, 1), (1, 1), (1, 3), (2, 1), (2, 2), (3, 2), (20, 3), (29, 2)])
def test_order_no_element_lost(grade_mod, nfiles, repeats):
    rows = _rows(nfiles, repeats)
    for name in ("a", "b", "cha0620", ""):
        out = grade_mod.order(rows, name)
        assert Counter(_keys(out)) == Counter(_keys(rows))


@pytest.mark.parametrize("nfiles,repeats", [(1, 3), (3, 2), (4, 3), (20, 3), (29, 2)])
def test_order_keeps_same_photo_adjacent(grade_mod, nfiles, repeats):
    """page() 가 붙어 있는 줄을 카드 한 장으로 묶는다 — 같은 사진은 한 덩어리로만 나온다."""
    rows = _rows(nfiles, repeats)
    for k in range(20):
        files = [r["file"] for r in grade_mod.order(rows, f"n{k}")]
        runs = [f for i, f in enumerate(files) if i == 0 or files[i - 1] != f]
        assert len(runs) == len(set(runs)) == nfiles, f"n{k}"


def test_order_shuffles_photos_and_repeats_within_photo(grade_mod):
    rows = _rows(10, 2)
    outs = [grade_mod.order(rows, f"rater{k}") for k in range(20)]
    assert len({tuple(r["file"] for r in o[::2]) for o in outs}) >= 15           # 사진 순서
    firsts = Counter(o[0]["repeat"] for o in outs) + Counter(o[2]["repeat"] for o in outs)
    assert set(firsts) == {1, 2}                                                  # 카드 안 A · B 순서


# ── apply ──────────────────────────────────────
def test_apply_flags_and_reviewed(grade_mod):
    sheet = {}
    row = grade_mod.apply(sheet, ("a.webp", 1), {"text_changed": True, "framing_issue": 1,
                                                 "wear_changed": False, "reviewed": True})
    assert row["text_changed"] == "1" and row["framing_issue"] == "1"
    assert row["wear_changed"] == "" and row["shape_color_changed"] == "" and row["background_issue"] == ""
    assert row["reviewed"] == "y"
    assert sheet[("a.webp", 1)] is row


def test_apply_missing_fields_are_blank(grade_mod):
    row = grade_mod.apply({}, ("a.webp", 1), {})
    assert row["reviewed"] == "" and row["note"] == ""
    assert all(row[f] == "" for f, _ in grade_mod.FLAGS)


def test_apply_unchecking_clears_previous_flag_keeps_other_cols(grade_mod):
    sheet = {("a.webp", 1): {"file": "a.webp", "repeat": "1", "mode": "generate", "text_changed": "1",
                              "reviewed": "y", "note": "old"}}
    row = grade_mod.apply(sheet, ("a.webp", 1), {"reviewed": True, "note": None})
    assert row["text_changed"] == "" and row["note"] == ""
    assert row["mode"] == "generate" and row["file"] == "a.webp"


def test_apply_does_not_mutate_old_row_object(grade_mod):
    old = {"text_changed": "1"}
    sheet = {("a", 1): old}
    grade_mod.apply(sheet, ("a", 1), {})
    assert old == {"text_changed": "1"}


@pytest.mark.parametrize("note,expected", [
    ("줄\n바꿈", "줄 바꿈"),
    ("  앞뒤  공백  ", "앞뒤 공백"),
    ("a\r\n\tb   c", "a b c"),
    ("\n\n", ""),
    (123, "123"),
    ("쉼표, 있음", "쉼표, 있음"),
])
def test_apply_note_whitespace_normalized(grade_mod, note, expected):
    assert grade_mod.apply({}, ("a", 1), {"note": note})["note"] == expected


# ── read_sheet ─────────────────────────────────
def test_read_sheet_missing_file_is_empty(grade_mod, tmp_path):
    assert grade_mod.read_sheet(tmp_path / "nope.csv") == {}


def test_read_sheet_accepts_bom_and_skips_bad_repeat(grade_mod, tmp_path):
    p = tmp_path / "r.csv"
    p.write_bytes("\ufefffile,repeat,reviewed\na.webp,1,y\nb.webp,,y\nc.webp,x,y\nd.webp, 2 ,y\n".encode("utf-8"))
    sheet = grade_mod.read_sheet(p)
    assert set(sheet) == {("a.webp", 1), ("d.webp", 2)}
    assert sheet[("a.webp", 1)]["reviewed"] == "y"


def test_read_sheet_short_row_and_missing_repeat_column(grade_mod, tmp_path):
    p = tmp_path / "r.csv"
    p.write_text("file,repeat,reviewed\na.webp\nb.webp,3,y\n", encoding="utf-8")
    assert set(grade_mod.read_sheet(p)) == {("b.webp", 3)}     # 짧은 줄은 repeat=None → 건너뜀
    p.write_text("file,reviewed\na.webp,y\n", encoding="utf-8")
    assert grade_mod.read_sheet(p) == {}


# ── write_sheet ────────────────────────────────
def _no_tmp(d):
    return not [p for p in d.iterdir() if p.name.endswith(".tmp")]


def test_write_sheet_all_rows_in_results_order(grade_mod, tmp_path):
    rows = _rows(3, 2)
    rows[0]["mode"] = "generate"
    p = tmp_path / "r.csv"
    grade_mod.write_sheet(p, rows, {})
    with open(p, encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert tuple(got[0].keys()) == grade_mod.COLS
    assert [(r["file"], int(r["repeat"])) for r in got] == _keys(rows)
    assert got[0]["mode"] == "generate" and got[1]["mode"] == "composite"
    assert all(r["reviewed"] == "" for r in got)
    assert _no_tmp(tmp_path)


def test_write_sheet_mode_missing_is_blank(grade_mod, tmp_path):
    p = tmp_path / "r.csv"
    grade_mod.write_sheet(p, [{"file": "a.webp", "repeat": 1}], {})
    assert grade_mod.read_sheet(p)[("a.webp", 1)]["mode"] == ""


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


def test_write_sheet_file_and_mode_come_from_results(grade_mod, tmp_path):
    p = tmp_path / "r.csv"
    grade_mod.write_sheet(p, [{"file": "a.webp", "repeat": 1, "mode": "generate"}],
                          {("a.webp", 1): {"mode": "composite", "reviewed": "y", "extra_col": "무시"}})
    r = grade_mod.read_sheet(p)[("a.webp", 1)]
    assert r["mode"] == "generate" and r["reviewed"] == "y" and "extra_col" not in r


def test_write_sheet_keeps_rows_not_in_results_at_end(grade_mod, tmp_path):
    p = tmp_path / "r.csv"
    sheet = {("ghost.webp", 9): {"file": "ghost.webp", "repeat": "9", "reviewed": "y", "note": "옛 실행"}}
    grade_mod.write_sheet(p, [{"file": "a.webp", "repeat": 1}], sheet)
    with open(p, encoding="utf-8") as f:
        got = list(csv.DictReader(f))
    assert [(r["file"], r["repeat"]) for r in got] == [("a.webp", "1"), ("ghost.webp", "9")]
    assert got[1]["reviewed"] == "y" and got[1]["note"] == "옛 실행"


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


def test_load_rows_skips_errors_and_analyze_only(grade_mod, fake_run):
    rows = grade_mod.load_rows(RUN_ID)
    assert len(rows) == 6
    assert {r["file"] for r in rows} == {"none_mug.webp", "doc_album.webp", "edge_shoe.webp"}


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


def test_page_token_is_json_escaped(grade_mod):
    text = grade_mod.page(RUN_ID, NAME, [], {}, {}, '"</script>')
    assert 'const TOKEN="\\"</script>";' in text   # json.dumps — 따옴표는 막지만 </script> 는 그대로 (토큰은 hex 라 무해)


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


def _raw(port, data: bytes) -> bytes:
    with socket.create_connection(("127.0.0.1", port), timeout=5) as sk:
        sk.sendall(data)
        out = b""
        while chunk := sk.recv(4096):
            out += chunk
    return out


def test_get_index_lists_all_cards_without_file_names(server):
    text = server["html"]
    assert text.count('<section class="card') == len(server["rows"]) == 6
    for leak in ("none_", "doc_", "edge_", "__r", "err_x"):
        assert leak not in text, leak
    assert _req(server["port"], "GET", "/?x=1")[0] == 200


def test_token_differs_per_handler(grade_mod, fake_run, tmp_path):
    rows = grade_mod.load_rows(RUN_ID)
    toks = set()
    for _ in range(2):
        h = grade_mod.make_handler(RUN_ID, NAME, rows, {}, tmp_path / "x.csv")
        srv = ThreadingHTTPServer(("127.0.0.1", 0), h)
        threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        body = _req(srv.server_address[1], "GET", "/")[2].decode()
        toks.add(re.search(r'const TOKEN="([0-9a-f]+)";', body).group(1))
        srv.shutdown()
        srv.server_close()
    assert len(toks) == 2


def test_get_img_maps_id_to_ordered_item(server):
    for i, it in enumerate(server["items"], 1):
        status, ctype, body = _req(server["port"], "GET", f"/img/{i}/orig")
        assert status == 200 and ctype == "image/webp"
        assert body == f"ORIG {it['file']} {it['repeat']}".encode()
        status, ctype, body = _req(server["port"], "GET", f"/img/{i}/result")
        assert status == 200 and ctype == "image/jpeg"
        assert body == f"RES {it['file']} {it['repeat']}".encode()


@pytest.mark.parametrize("path", [
    "/img/0/orig", "/img/7/orig", "/img/-1/orig", "/img/1/other", "/img/1/orig/x", "/img/1",
    "/img/a/orig", "/img/%31/orig", "/img/1/../results.jsonl",
    "/files/none_mug__r1_orig.webp",      # 옛 경로
    "/files/../results.jsonl", "/files/%2e%2e/results.jsonl",
    "/results.jsonl", "/secret.txt", "/img/", "/favicon.ico",
])
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


# 요청 줄이 latin-1 로 풀려 0xB2 가 '²' 가 된다 — isdigit() 는 True 지만 int() 는 실패 (ASCII 만 허용)
def test_get_img_unicode_digit_404(server):
    resp = _raw(server["port"], b"GET /img/\xb2/orig HTTP/1.0\r\nHost: x\r\n\r\n")
    assert resp.startswith(b"HTTP/1.0 404")


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
    ("", "application/json"),
    (None, "text/plain"),                 # 맞는 토큰 · 틀린 형식
    (None, None),                         # Content-Type 없음
    (None, "application/x-www-form-urlencoded"),
])
def test_post_save_requires_token_and_json_403(server, token, ctype):
    status, _, _ = _save(server, {"id": 1, "reviewed": True}, token=token, ctype=ctype)
    assert status == 403
    assert not server["sheet"].exists()


def test_post_save_json_with_charset_ok(server):
    assert _save(server, {"id": 1, "reviewed": True}, ctype="application/json; charset=utf-8")[0] == 200


@pytest.mark.parametrize("payload", [
    {"id": 0, "reviewed": True},
    {"id": 7, "reviewed": True},
    {"id": -1, "reviewed": True},
    {"id": "abc"},
    {"id": None},
    {"id": [1]},
    {"file": "none_mug.webp", "repeat": 1, "reviewed": True},   # 옛 형식
    {},
    b"{not json",
    b"[]",
    b'"str"',
    b"\xff\xfe\x00",
])
def test_post_save_bad_body_400(server, payload):
    status, _, _ = _save(server, payload)
    assert status == 400
    assert not server["sheet"].exists()


def _post_with_length(s, length: str, body: bytes = b"{}") -> int:
    c = http.client.HTTPConnection("127.0.0.1", s["port"], timeout=5)
    c.putrequest("POST", "/api/save")
    c.putheader("Content-Type", "application/json")
    c.putheader("X-Grade-Token", s["token"])
    c.putheader("Content-Length", length)
    c.endheaders(body)
    status = c.getresponse().status
    c.close()
    return status


@pytest.mark.parametrize("length", ["0", "-5", str(64 * 1024 + 1), "abc"])
def test_post_save_bad_content_length_400(server, length):
    assert _post_with_length(server, length) == 400
    assert not server["sheet"].exists()


def test_post_save_empty_body_400(server):
    assert _save(server, b"")[0] == 400


def test_post_save_body_at_limit_ok(server, grade_mod):
    base = json.dumps({"id": 1, "reviewed": True, "note": ""}).encode()
    note = "a" * (grade_mod.MAX_BODY - len(base))
    body = json.dumps({"id": 1, "reviewed": True, "note": note}).encode()
    assert len(body) == grade_mod.MAX_BODY
    assert _save(server, body)[0] == 200


def test_post_other_path_404(server):
    assert _req(server["port"], "POST", "/api/other", b"{}",
                {"Content-Type": "application/json", "X-Grade-Token": server["token"]})[0] == 404


def test_concurrent_saves_keep_all_rows(server, grade_mod):
    n = len(server["items"])
    ts = [threading.Thread(target=_save, args=(server, {"id": i, "reviewed": True})) for i in range(1, n + 1)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    sheet = grade_mod.read_sheet(server["sheet"])
    assert len(sheet) == n and all(r["reviewed"] == "y" for r in sheet.values())


def test_save_keeps_hand_edited_bom_sheet_and_stale_rows(server, grade_mod):
    """엑셀로 저장한(BOM) 시트 · 다른 실행에서 남은 줄이 있어도 이어서 쓴다."""
    other = server["items"][1]                       # 저장할 id 1 과 다른 줄
    def line(**v):
        return ",".join(str(v.get(c, "")) for c in grade_mod.COLS) + "\n"
    server["sheet"].write_bytes(("\ufeff" + ",".join(grade_mod.COLS) + "\n"
                                 + line(file=other["file"], repeat=other["repeat"], mode="composite", reviewed="y",
                                        shape_color_changed=1, note="손으로")
                                 + line(file="old.webp", repeat=1, mode="generate", reviewed="y", note="옛 줄")
                                 ).encode("utf-8"))
    assert _save(server, {"id": 1, "reviewed": True})[0] == 200
    sheet = grade_mod.read_sheet(server["sheet"])
    hand = sheet[(other["file"], int(other["repeat"]))]
    assert hand["note"] == "손으로" and hand["shape_color_changed"] == "1"
    first = server["items"][0]
    assert sheet[(first["file"], int(first["repeat"]))]["reviewed"] == "y"
    assert sheet[("old.webp", 1)]["note"] == "옛 줄"
    assert not server["sheet"].read_bytes().startswith(b"\xef\xbb\xbf")   # 다시 쓸 땐 BOM 없음
    assert list(sheet)[-1] == ("old.webp", 1)


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


# ── 10-01: 상품 사진 품질 ──────────────────────
@pytest.mark.parametrize("raw,expected", [("5", "5"), (3, "3"), (" 1 ", "1"), ("0", ""), ("6", ""),
                                          ("", ""), (None, ""), ("4.5", ""), ("<b>", "")])
def test_apply_photo_quality_only_1_to_5(grade_mod, raw, expected):
    row = grade_mod.apply({}, ("a.webp", 1), {"reviewed": True, "photo_quality": raw})
    assert row["photo_quality"] == expected


def test_page_has_added_content_flag_and_quality_radios(grade_mod, fake_run):
    items = grade_mod.order(grade_mod.load_rows(RUN_ID), NAME)
    sheet = {(items[0]["file"], int(items[0]["repeat"])): {"photo_quality": "4", "reviewed": "y"}}
    text = grade_mod.page(RUN_ID, NAME, items, sheet, {}, "t")
    assert text.count('name="added_content"') == 6
    assert text.count('type="radio" name="q1"') == 5 and text.count('type="radio" name="q2"') == 5
    assert 'name="q1" value="4" checked' in text and 'name="q2" value="4" checked' not in text


def test_post_save_writes_photo_quality(server, grade_mod):
    assert _save(server, {"id": 1, "reviewed": True, "added_content": True, "photo_quality": "2"})[0] == 200
    it = server["items"][0]
    row = grade_mod.read_sheet(server["sheet"])[(it["file"], int(it["repeat"]))]
    assert row["photo_quality"] == "2" and row["added_content"] == "1"


# ── 10-02: 실패 원인 태그 (failure_tags) ──────────
def test_cols_has_failure_tags_and_tag_keys_match(grade_mod):
    assert "failure_tags" in grade_mod.COLS
    assert grade_mod.TAG_KEYS == tuple(k for k, _ in grade_mod.FAILURE_TAGS)
    assert len(set(grade_mod.TAG_KEYS)) == len(grade_mod.TAG_KEYS)
    assert all(";" not in k and k == k.strip() and k for k in grade_mod.TAG_KEYS)   # 구분자와 안 겹친다


def test_apply_failure_tags_in_tag_keys_order(grade_mod):
    keys = grade_mod.TAG_KEYS
    row = grade_mod.apply({}, ("a.webp", 1), {"reviewed": True, "failure_tags": [keys[3], keys[0], keys[-1]]})
    assert row["failure_tags"] == ";".join([keys[0], keys[3], keys[-1]])


def test_apply_failure_tags_all_tags(grade_mod):
    row = grade_mod.apply({}, ("a.webp", 1), {"failure_tags": list(reversed(grade_mod.TAG_KEYS))})
    assert row["failure_tags"] == ";".join(grade_mod.TAG_KEYS)


@pytest.mark.parametrize("raw", [
    None, "", "detail_lost", "detail_lost;color_changed", {"detail_lost": True}, ("detail_lost",),
    {"detail_lost"}, 1, True, [],
])
def test_apply_failure_tags_non_list_or_empty_is_blank(grade_mod, raw):
    row = grade_mod.apply({}, ("a.webp", 1), {"reviewed": True, "failure_tags": raw})
    assert row["failure_tags"] == ""


def test_apply_failure_tags_missing_key_is_blank(grade_mod):
    assert grade_mod.apply({}, ("a.webp", 1), {"reviewed": True})["failure_tags"] == ""


def test_apply_failure_tags_drops_unknown_and_non_strings(grade_mod):
    data = {"failure_tags": ["color_changed", "unknown", "", " detail_lost", "DETAIL_LOST", "detail_lost;x",
                             None, 1, ["detail_lost"], {"t": 1}, "<script>", "color_changed", "tag_lost"]}
    row = grade_mod.apply({}, ("a.webp", 1), data)
    assert row["failure_tags"] == "color_changed;tag_lost"     # 중복은 한 번, 공백·대소문자 다른 건 버림


def test_apply_failure_tags_resave_without_tags_clears_previous(grade_mod):
    key = ("a.webp", 1)
    sheet = {key: {"failure_tags": "detail_lost;color_changed", "mode": "generate"}}
    row = grade_mod.apply(sheet, key, {"reviewed": True})
    assert row["failure_tags"] == "" and row["mode"] == "generate"
    assert sheet[key]["failure_tags"] == ""


def test_write_read_sheet_roundtrips_failure_tags(grade_mod, tmp_path):
    rows = _rows(1, 2)
    sheet = {}
    grade_mod.apply(sheet, ("f0.webp", 1), {"reviewed": True, "failure_tags": ["tag_lost", "detail_lost"]})
    path = tmp_path / "s.csv"
    grade_mod.write_sheet(path, rows, sheet)
    back = grade_mod.read_sheet(path)
    assert back[("f0.webp", 1)]["failure_tags"] == "detail_lost;tag_lost"
    assert back[("f0.webp", 2)]["failure_tags"] == ""
    with open(path, encoding="utf-8") as f:
        assert next(csv.reader(f)) == list(grade_mod.COLS)


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


def test_page_has_tag_checkboxes_per_card(grade_mod, fake_run):
    items = grade_mod.order(grade_mod.load_rows(RUN_ID), NAME)
    text = grade_mod.page(RUN_ID, NAME, items, {}, {}, "t")
    n = len(grade_mod.FAILURE_TAGS)
    assert text.count("data-tag=") == n * len(items)
    for k in grade_mod.TAG_KEYS:
        assert text.count(f'data-tag="{k}"') == len(items)
    assert "data-tag" not in text.split("<main>")[0]             # 헤더엔 없다
    assert " checked> " not in text                               # 아무 것도 저장 안 됨
    assert "<details open>" not in text and text.count("<details>") == len(items)
    assert "body.failure_tags=" in text                           # JS 가 태그를 보낸다


def test_page_checks_saved_tags_and_opens_details(grade_mod, fake_run):
    items = grade_mod.order(grade_mod.load_rows(RUN_ID), NAME)
    k0 = (items[0]["file"], int(items[0]["repeat"]))
    k1 = (items[1]["file"], int(items[1]["repeat"]))
    sheet = {k0: {"failure_tags": "detail_lost;tag_lost", "reviewed": "y"},
             k1: {"failure_tags": "", "reviewed": "y"}}
    text = grade_mod.page(RUN_ID, NAME, items, sheet, {}, "t")
    cards = re.split(r'<section class="card', text)[1:]
    assert len(cards) == len(items)
    c0 = cards[0]
    assert 'data-tag="detail_lost" checked' in c0 and 'data-tag="tag_lost" checked' in c0
    assert c0.count(" checked") == 2 and "<details open>" in c0
    for c in cards[1:]:
        assert "<details open>" not in c and 'data-tag="detail_lost" checked' not in c
    assert text.count("<details open>") == 1


def test_page_unknown_saved_tag_opens_details_but_checks_nothing(grade_mod, fake_run):
    """현재 동작 기록: 손으로 고친 모르는 태그는 체크되지 않지만 details 는 열린다."""
    items = grade_mod.order(grade_mod.load_rows(RUN_ID), NAME)
    k0 = (items[0]["file"], int(items[0]["repeat"]))
    text = grade_mod.page(RUN_ID, NAME, items, {k0: {"failure_tags": "mystery"}}, {}, "t")
    c0 = re.split(r'<section class="card', text)[1]
    assert "<details open>" in c0 and "data-tag" in c0 and 'checked' not in c0
    assert "mystery" not in text


def test_page_tags_hand_edited_with_spaces_and_commas_checked(grade_mod, fake_run):
    """손으로 고친 칸 (공백 · "," 구분) 도 split_tags 로 체크한다."""
    items = grade_mod.order(grade_mod.load_rows(RUN_ID), NAME)
    k0 = (items[0]["file"], int(items[0]["repeat"]))
    text = grade_mod.page(RUN_ID, NAME, items, {k0: {"failure_tags": " detail_lost ; tag_lost, color_changed"}}, {}, "t")
    c0 = re.split(r'<section class="card', text)[1]
    for k in ("detail_lost", "tag_lost", "color_changed"):
        assert f'data-tag="{k}" checked' in c0
    assert c0.count(" checked") == 3


@pytest.mark.parametrize("raw,expected", [
    (None, []), ("", []), (" ", []), (";", []), (",;,", []),
    ("a", ["a"]), ("a;b", ["a", "b"]), (" a , b ;c ", ["a", "b", "c"]),
    ("a;;b", ["a", "b"]), ("b;a;b", ["b", "a", "b"]),          # 순서·중복은 그대로 (거르는 건 쓰는 쪽)
    (0, []), (123, ["123"]),
])
def test_split_tags(grade_mod, raw, expected):
    assert grade_mod.split_tags(raw) == expected


def test_apply_keeps_unknown_hand_written_tags_at_end(grade_mod):
    key = ("a.webp", 1)
    sheet = {key: {"failure_tags": "typo_tag; detail_lost ,old_tag"}}
    row = grade_mod.apply(sheet, key, {"reviewed": True, "failure_tags": ["tag_lost", "browser_unknown"]})
    # 아는 태그는 브라우저 값으로 갈아 끼우고(detail_lost 는 해제됨), 모르는 손글씨 태그는 뒤에 남는다
    assert row["failure_tags"] == "tag_lost;typo_tag;old_tag"
    # 다시 저장해도 모르는 태그는 그대로, 두 번 붙지 않는다
    row = grade_mod.apply(sheet, key, {"reviewed": True, "failure_tags": []})
    assert row["failure_tags"] == "typo_tag;old_tag"


def test_apply_browser_unknown_tag_still_dropped(grade_mod):
    row = grade_mod.apply({}, ("a.webp", 1), {"failure_tags": ["browser_unknown", "tag_lost"]})
    assert row["failure_tags"] == "tag_lost"


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
