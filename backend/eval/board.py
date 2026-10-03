"""eval 결과판 — 모든 실행의 결과를 사진별로 한눈에, 실패 모음에 넣을 사진을 체크한다.

    cd backend
    python eval/board.py                 # http://localhost:8766

- 첫 페이지: 사진마다 원본 + 모든 실행의 결과 (사람 판정 배지 · 채점 메모). 보존 실패 비율이 높은 사진부터
  (모든 실행을 합친 비율 — 옛 실행 · 기각한 실험도 들어간다)
- 사진마다 "실패 모음" 체크 → dataset.json 의 set="failure" 를 바로 고친다 (해제하면 set · failure_added 를 지운다, 태그 · 근거 메모는 남긴다)
- 그 밖에 여는 것: runs/*.html (compare · failures), runs/<id>/review.html · report.md, runs/<id>/files/ 의 사진만
  (results.jsonl · carret.db · storage/ · 폴더 목록은 열지 않는다)

사람 판정은 report.py 와 같은 규칙 (물건 표시 없으면 보존 통과, 평가자 여럿이면 다수결 · 동점은 실패, 오류난 회차는 판정 안 함).
로컬 전용: 127.0.0.1 에만 열고, Host 가 localhost · 127.0.0.1 · Codespaces 포워딩 주소가 아니면 거절한다 (DNS rebinding).
"""
import argparse
import contextlib
import csv
import html
import json
import secrets
import sys
import threading
from collections import defaultdict
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import intake  # noqa: E402
import report as rp  # noqa: E402

MAX_BODY = 4096
_LOCK = threading.Lock()
IMAGE_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp"}
ALLOWED_HOST_SUFFIXES = (".app.github.dev",)    # Codespaces 포트 포워딩


# ── 모으기 ──────────────────────────────────────
def _notes(run_id: str) -> dict:
    """{(file, repeat): ["평가자: 메모"]} — 채점표의 note 칸."""
    out = defaultdict(list)
    for p in sorted((HERE / "reviews" / run_id).glob("*.csv")):
        if p.stem == "TEMPLATE":
            continue
        try:
            with open(p, encoding="utf-8-sig") as f:
                rows = list(csv.DictReader(f))
        except (OSError, UnicodeDecodeError, csv.Error):
            continue                     # 깨진 채점표 하나 때문에 결과판이 안 열리면 안 된다
        for r in rows:
            note = (r.get("note") or "").strip()
            if not note or not rp.flagged(r.get("reviewed")):   # 판정에 안 들어가는 줄의 메모는 안 붙인다
                continue
            try:
                key = (r["file"], int(r["repeat"]))
            except (KeyError, TypeError, ValueError):
                continue
            out[key].append(f"{p.stem}: {note}")
    return out


def _results(run_id: str) -> list[dict]:
    """results.jsonl — 깨진 줄(중간에 멈춘 실행의 마지막 줄 등)은 건너뛴다."""
    out = []
    try:
        text = (HERE / "runs" / run_id / "results.jsonl").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return out
    for line in text.splitlines():
        with contextlib.suppress(json.JSONDecodeError):
            r = json.loads(line) if line.strip() else None
            if isinstance(r, dict):
                out.append(r)
    return out


def collect() -> list[dict]:
    """사진별 [{file, entry, orig, results:[{run, repeat, mode, result, preserved, flags, tags, notes}]}]."""
    dataset = {e["file"]: e for e in intake.load_dataset()}
    by_file: dict[str, dict] = {}
    runs = sorted(p.name for p in (HERE / "runs").glob("*") if (p / "results.jsonl").exists())
    for run_id in runs:
        rows = _results(run_id)
        if not any(r.get("result") or r.get("error") for r in rows):
            continue              # analyze 만 한 실행은 결과 사진이 없다 (전부 오류난 실행은 보여 준다)
        try:
            reviews = rp.load_reviews(run_id)
        except (OSError, UnicodeDecodeError, csv.Error):
            reviews = {}
        notes = _notes(run_id)
        seen = set()
        for r in rows:
            f = r.get("file")
            if not isinstance(f, str) or type(r.get("repeat")) is not int or (f, r["repeat"]) in seen:
                continue
            key = (f, r["repeat"])
            seen.add(key)
            # 오류난 회차는 판정하지 않는다 (report.py 는 오류 없는 줄만 본다 — 옛 채점 줄이 남아 있어도)
            vs = [] if r.get("error") else [reviews[p][key] for p in sorted(reviews) if key in reviews[p]]
            item = by_file.setdefault(f, {"file": f, "entry": dataset.get(f, {}), "orig": None, "results": []})
            if not item["orig"] and r.get("orig") and _image(run_id, r["orig"]):
                item["orig"] = (run_id, r["orig"])
            flags = sorted({x for v in vs for x in v["flags"]})
            item["results"].append({
                "run": run_id, "repeat": r["repeat"], "mode": r.get("mode"), "result": r.get("result"),
                "error": r.get("error"),
                "preserved": rp.majority([v["preserved"] for v in vs]),
                "flags": [x for x in flags if x in rp.OBJECT_FLAGS],
                "quality_flags": [x for x in flags if x not in rp.OBJECT_FLAGS],
                "tags": sorted({x for v in vs for x in v.get("tags", ())}),
                "raters": [] if r.get("error") else sorted(p for p in reviews if key in reviews[p]),
                "notes": [] if r.get("error") else notes.get(key, [])})
    for f, e in dataset.items():      # 아직 안 돌린 사진도 체크할 수 있게
        by_file.setdefault(f, {"file": f, "entry": e, "orig": None, "results": []})

    def rank(it):
        rated = [x for x in it["results"] if x["preserved"] is not None]
        fails = sum(not x["preserved"] for x in rated)
        return (-(fails / len(rated)) if rated else 1, -fails, it["file"])
    return sorted(by_file.values(), key=rank)


def set_failure(file: str, on: bool) -> bool:
    """dataset.json 의 실패 모음 표시를 고친다. 모르는 사진이면 False."""
    with _LOCK, intake.dataset_lock():
        entries = intake.load_dataset()
        e = next((x for x in entries if x.get("file") == file), None)
        if e is None:
            return False
        if on and e.get("set") != "failure":
            e["set"] = "failure"
            e["failure_added"] = date.today().isoformat()
        elif not on and e.get("set") == "failure":     # 다른 set 값은 건드리지 않는다
            e.pop("set", None)
            e.pop("failure_added", None)
        else:
            return True                                # 이미 그 상태 — 쓰지 않는다 (낡은 탭이 날짜를 바꾸지 않게)
        intake.save_dataset(entries)
    return True


# ── 파일 ────────────────────────────────────────
def _image(run_id: str, rel) -> Path | None:
    """runs/<run_id>/files/ 아래 실제 사진만 (심볼릭 링크로 밖을 가리키면 None)."""
    rel = str(rel or "")
    if not rel or not run_id or "/" in run_id or run_id in (".", ".."):
        return None
    runs = (HERE / "runs").resolve()
    run_dir, files = HERE / "runs" / run_id, HERE / "runs" / run_id / "files"
    if run_dir.is_symlink() or files.is_symlink():      # files/ 자체가 밖을 가리키는 링크면 막는다
        return None
    base = runs / run_id / "files"
    p = (HERE / "runs" / run_id / rel).resolve()
    return p if p.is_relative_to(base) and p.is_file() and p.suffix.lower() in IMAGE_TYPES else None


def static_file(url_path: str) -> Path | None:
    """열어 주는 파일: runs/*.html · runs/<id>/review.html · report.md · runs/<id>/files/<사진>. 나머지는 None."""
    from urllib.parse import unquote
    parts = [x for x in unquote(url_path).split("/") if x]
    runs = (HERE / "runs").resolve()
    if len(parts) == 1 and parts[0].endswith(".html"):
        p = (runs / parts[0]).resolve()
        return p if p.parent == runs and p.is_file() else None
    if len(parts) == 2 and parts[1] in ("review.html", "report.md"):
        p = (runs / parts[0] / parts[1]).resolve()
        return p if p.parent.parent == runs and p.is_file() else None
    if len(parts) >= 3 and parts[1] == "files":
        return _image(parts[0], "/".join(parts[1:]))
    return None


# ── 페이지 ──────────────────────────────────────
def _src(run_id: str, rel) -> str | None:
    rel = str(rel or "")
    if not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
        return None
    return html.escape("/" + quote(f"{run_id}/{rel}"), quote=True)


def page(items: list[dict], token: str) -> str:
    e = html.escape
    runs = sorted({x["run"] for it in items for x in it["results"]})
    cards = []
    for it in items:
        rated = [x for x in it["results"] if x["preserved"] is not None]
        fails = sum(not x["preserved"] for x in rated)
        ent = it["entry"]
        on = ent.get("set") == "failure"
        o = _src(*it["orig"]) if it["orig"] else None
        orig_img = f'<img loading="lazy" src="{o}">' if o else '<div class="small muted">원본 없음</div>'
        figs = [f'<figure><div class="who">원본</div>{orig_img}</figure>']
        for x in it["results"]:
            s = _src(x["run"], x["result"]) if not x["error"] else None
            state = {None: ("none", "채점 전"), True: ("good", "보존 ✓"), False: ("bad", "보존 ✗")}[x["preserved"]]
            detail = ", ".join(x["flags"] + x["tags"])
            qual = ", ".join(x["quality_flags"])
            figs.append(
                f'<figure class="{state[0]}"><div class="who">{e(x["run"])} r{x["repeat"]}</div>'
                + (f'<a href="{s}" target="_blank"><img loading="lazy" src="{s}"></a>' if s
                   else f'<div class="err">{e(str(x["error"] or "이미지 없음")[:80])}</div>')
                + f'<div class="badge {state[0]}">{e(str(x["mode"] or ""))} · {state[1]}</div>'
                + (f'<div class="small">{e(detail)}</div>' if detail else "")
                + (f'<div class="small muted">배경·구도: {e(qual)}</div>' if qual else "")
                + (f'<div class="small muted">채점 {e(", ".join(x["raters"]))}</div>' if x["raters"] else "")
                + "".join(f'<div class="small note">{e(n)}</div>' for n in x["notes"])
                + "</figure>")
        cards.append(
            f'<section class="item{" fail" if fails else ""}" data-fails="{fails}">'
            f'<div class="head"><label class="pick"><input type="checkbox" data-file="{e(it["file"], quote=True)}"'
            f'{" checked" if on else ""}> 실패 모음</label>'
            f'<h2>{e(it["file"])}</h2><span class="muted">보존 실패 {fails} / 채점 {len(rated)} · 결과 {len(it["results"])}개'
            f' · {e(str(ent.get("item", "")))}</span></div>'
            + (f'<p class="muted small">{e(str(ent.get("note", "")))}</p>' if ent.get("note") else "")
            + f'<div class="grid">{"".join(figs)}</div></section>')
    links = " · ".join(f'<a href="/{e(quote(r), quote=True)}/review.html">{e(r)}</a>' for r in runs)
    compares = sorted(p.name for p in (HERE / "runs").glob("compare-*.html"))
    clinks = " · ".join(f'<a href="/{e(quote(c), quote=True)}">{e(c[8:-5])}</a>' for c in compares)
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>eval 결과판</title>
<style>
:root{{--bg:#f6f6f4;--card:#fff;--fg:#222;--muted:#666;--line:#ddd;--good:#2e7d32;--bad:#c62828;--badbg:#fdecec}}
@media (prefers-color-scheme:dark){{:root{{--bg:#18181a;--card:#222226;--fg:#eee;--muted:#aaa;--line:#3a3a40;--good:#81c784;--bad:#ef9a9a;--badbg:#3a2224}}}}
body{{font:14px system-ui,sans-serif;margin:0;background:var(--bg);color:var(--fg)}} main{{padding:16px;max-width:1600px;margin:auto}}
a{{color:inherit}} header{{position:sticky;top:0;background:var(--bg);padding:8px 0;z-index:1;border-bottom:1px solid var(--line);margin-bottom:12px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:0 0 16px}}
.head{{display:flex;gap:12px;align-items:baseline;flex-wrap:wrap}} h2{{font-size:16px;margin:0}}
.pick{{font-weight:600;cursor:pointer;padding:2px 8px;border:1px solid var(--line);border-radius:6px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:8px;margin-top:8px}}
figure{{margin:0;padding:4px;border-radius:6px}} figure.bad{{background:var(--badbg)}}
img{{width:100%;border-radius:6px;border:1px solid var(--line);background:#fff}}
.who{{font-size:11px;font-weight:600;margin-bottom:2px;word-break:break-all}} .small{{font-size:11px}} .muted{{color:var(--muted)}}
.badge{{font-size:12px}} .badge.good{{color:var(--good)}} .badge.bad{{color:var(--bad);font-weight:600}} .badge.none{{color:var(--muted)}}
.err{{font-size:11px;color:var(--bad)}} .note{{margin-top:2px}} body.onlyfail section.item:not(.fail){{display:none}}
#msg{{margin-left:8px;color:var(--muted)}}
</style>
<main>
<header><b>eval 결과판</b> — 사진 {len(items)}장 · 실패 모음 <span id="cnt">{sum(it["entry"].get("set") == "failure" for it in items)}</span>장
 <label><input type="checkbox" id="onlyfail"> 실패 있는 사진만</label><span id="msg"></span></header>
<section class="small"><div>실행별 원본·결과·프롬프트: {links or "없음"}</div><div>비교 페이지: {clinks or "없음"}</div>
<div class="muted">보존 판정은 사람 채점 다수결 (동점은 실패). 정렬은 모든 실행을 합친 실패 비율 순 — 옛 실행 · 기각한 실험도 들어간다. 체크하면 dataset.json 에 바로 저장된다.</div></section>
{"".join(cards)}
</main>
<script>
const TOKEN = {json.dumps(token)};
document.getElementById('onlyfail').addEventListener('change', ev => document.body.classList.toggle('onlyfail', ev.target.checked));
const msg = document.getElementById('msg'), cnt = document.getElementById('cnt');
document.querySelectorAll('input[data-file]').forEach(box => box.addEventListener('change', async () => {{
  box.disabled = true;
  try {{
    const r = await fetch('/api/failure', {{method: 'POST', headers: {{'Content-Type': 'application/json', 'X-Board-Token': TOKEN}},
      body: JSON.stringify({{file: box.dataset.file, on: box.checked}})}});
    if (!r.ok) throw new Error(await r.text());
    cnt.textContent = document.querySelectorAll('input[data-file]:checked').length;
    msg.textContent = '저장됨: ' + box.dataset.file + (box.checked ? ' → 실패 모음' : ' → 뺌');
  }} catch (err) {{ box.checked = !box.checked; msg.textContent = '저장 실패: ' + err.message; }}
  box.disabled = false;
}}));
</script>"""


# ── 서버 ────────────────────────────────────────
def make_handler(token: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str, head: bool = False):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _host_ok(self) -> bool:
            host = (self.headers.get("Host") or "").rsplit(":", 1)[0].lower()
            return host in ("localhost", "127.0.0.1") or host.endswith(ALLOWED_HOST_SUFFIXES)

        def _get(self, head: bool):
            if not self._host_ok():
                return self._send(403, b"forbidden host", "text/plain", head)
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                return self._send(200, page(collect(), token).encode(), "text/html; charset=utf-8", head)
            f = static_file(path)
            if f is None:
                return self._send(404, b"not found", "text/plain", head)
            ctype = IMAGE_TYPES.get(f.suffix.lower()) or (
                "text/html; charset=utf-8" if f.suffix == ".html" else "text/plain; charset=utf-8")
            return self._send(200, f.read_bytes(), ctype, head)

        def do_GET(self):
            self._get(head=False)

        def do_HEAD(self):
            self._get(head=True)

        def do_POST(self):
            if not self._host_ok():
                return self._send(403, b"forbidden host", "text/plain")
            if self.path != "/api/failure":
                return self._send(404, b"not found", "text/plain")
            if (self.headers.get("X-Board-Token") != token
                    or not (self.headers.get("Content-Type") or "").startswith("application/json")):
                return self._send(403, "페이지를 새로고침하세요".encode(), "text/plain; charset=utf-8")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= MAX_BODY:
                    raise ValueError(n)
                data = json.loads(self.rfile.read(n))
                file, on = data["file"], data["on"]
                if not isinstance(file, str) or not isinstance(on, bool):
                    raise TypeError
            except (ValueError, KeyError, TypeError):
                return self._send(400, "잘못된 요청".encode(), "text/plain; charset=utf-8")
            if not set_failure(file, on):
                return self._send(404, "dataset.json 에 없는 사진".encode(), "text/plain; charset=utf-8")
            self._send(200, b"ok", "text/plain")
    return Handler


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8766)
    a = ap.parse_args(argv)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(secrets.token_urlsafe(16)))
    print(f"결과판: http://localhost:{a.port}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
