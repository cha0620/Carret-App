"""브라우저 채점 — 원본 | 결과를 보면서 체크하면 results/reviews/<run_id>/<이름>.csv 에 바로 저장된다.

    cd backend
    python eval/grade.py 20261001-pilot-full2 --name cha0620     # http://localhost:8765

- 눈가림: 경로·분석값·자동 지표는 보여주지 않는다 (지켜야 할 글자와 라벨 메모만)
- 사진당 카드 한 장 — 원본은 한 번만, 그 옆에 repeat 결과들을 나란히 두고 결과마다 따로 체크한다
- 순서: 평가자 이름으로 섞는다 — 같은 사람은 매번 같은 순서. 사진 순서도, 카드 안 결과 순서(A · B)도 섞는다
- 화면·요청에는 순서 번호만 쓴다 (파일 이름에 층 코드 · repeat 가 들어 있다)
- 결과마다 보존 표시(물건이 바뀜) + 실패 유형(원인별 태그, 고르기) + 상품 사진 품질 1~5 (판매 페이지에 올릴 만한가 — 보존과 따로)
- 저장: 체크할 때마다 그 줄을 CSV 에 쓴다. 다시 열면 이어서 채운다
"""
import argparse
import csv
import hashlib
import html
import json
import os
import random
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
COLS = ("file", "repeat", "mode", "reviewed", "shape_color_changed", "text_changed",
        "wear_changed", "added_content", "background_issue", "framing_issue", "failure_tags", "photo_quality", "note")
FLAGS = (("shape_color_changed", "형태 · 색 · 무늬 · 부품이 바뀜"),
         ("text_changed", "지켜야 할 글자 · 로고가 바뀜"),
         ("wear_changed", "하자가 지워짐 · 생김"),
         ("added_content", "원본에 없던 물건 · 글자 · 부품이 생김"),
         ("background_issue", "배경 어색 · 원래 배경 조각 남음"),
         ("framing_issue", "잘림 · 너무 작음 · 워터마크 생김"))
# 실패 유형 — 위 표시보다 잘게, 같은 원인끼리 세려고 (CSV 에는 ";" 로 이어 쓴다). 표시 칸과 따로 고른다
FAILURE_TAGS = (("detail_lost", "디자인 디테일 사라짐 (구멍 · 워싱 · 자수 결)"),
                ("material_changed", "소재 · 질감 · 광택 바뀜"),
                ("color_changed", "색 바뀜"),
                ("part_changed", "부품 · 디자인 바뀜 (그릴 · 줄 · 바늘 위치 등)"),
                ("text_altered", "있던 글자 · 로고가 바뀜 · 뭉개짐"),
                ("text_added", "없던 글자 · 로고 · 자막 생김"),
                ("tag_lost", "택 · 라벨 · 포장 사라짐"),
                ("occlusion_invented", "가려졌던 곳을 지어냄"),
                ("object_added", "원본에 없던 다른 물건 생김"),
                ("wear_removed", "하자(얼룩 · 흠집)가 지워짐"))
TAG_KEYS = tuple(k for k, _ in FAILURE_TAGS)


def split_tags(value) -> list[str]:
    """CSV 칸 → 태그 목록. 손으로 고친 칸의 공백 · "," 구분도 받는다."""
    return [t.strip() for t in str(value or "").replace(",", ";").split(";") if t.strip()]
# 상품 사진 품질 — 보존과 따로 본다 (원본과 비교하지 말고 "이 사진을 판매 페이지에 그대로 올릴 만한가")
QUALITY = (("5", "쇼핑몰 공식 사진 수준"), ("4", "바로 올려도 됨"), ("3", "쓸 만하지만 어색한 곳이 보임"),
           ("2", "AI 티 · 어색함이 커서 망설여짐"), ("1", "못 씀"))
_LOCK = threading.Lock()
MAX_BODY = 64 * 1024


def load_rows(run_id: str) -> list[dict]:
    """results.jsonl 에서 채점할 줄 — 실패한 실행은 뺀다 (TEMPLATE.csv 와 같은 기준)."""
    rows = []
    with open(HERE / "results" / "runs" / run_id / "results.jsonl", encoding="utf-8") as f:
        for line in f:
            r = json.loads(line)
            if not r.get("error") and r.get("orig") and r.get("result"):   # analyze 만 돌린 줄은 이미지가 없다
                rows.append(r)
    return rows


def order(rows: list[dict], name: str) -> list[dict]:
    """이름으로 섞은 순서 — 사진 순서를 섞고, 같은 사진의 repeat 는 붙여 두되 그 안의 순서도 섞는다.
    page() 가 붙어 있는 같은 사진 줄을 카드 한 장으로 묶는다."""
    rng = random.Random(name)
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r["file"], []).append(r)
    files = sorted(groups)
    rng.shuffle(files)
    out = []
    for f in files:
        g = groups[f][:]
        rng.shuffle(g)
        out.extend(g)
    return out


def read_sheet(path: Path) -> dict[tuple[str, int], dict]:
    """손으로 고친 파일도 읽는다 — BOM(엑셀) 허용, repeat 가 이상한 줄은 건너뛴다."""
    if not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            try:
                out[(r["file"], int(r["repeat"]))] = r
            except (KeyError, TypeError, ValueError):
                continue
    return out


def write_sheet(path: Path, rows: list[dict], sheet: dict) -> None:
    """results 순서(TEMPLATE 와 같은 순서)로 전부 다시 쓴다 — 임시 파일에 쓰고 바꿔 끼워 반쯤 쓴 파일이 남지 않게.
    results 에 없는 줄(실행을 다시 해서 빠진 줄)도 지우지 않고 뒤에 남긴다."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=COLS, extrasaction="ignore")
            w.writeheader()
            seen = set()
            for r in rows:
                key = (r["file"], int(r["repeat"]))
                seen.add(key)
                old = sheet.get(key, {})
                w.writerow({c: old.get(c, "") for c in COLS}
                           | {"file": r["file"], "repeat": r["repeat"], "mode": r.get("mode")})
            for key, old in sheet.items():
                if key not in seen:
                    w.writerow({c: old.get(c, "") for c in COLS})
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def apply(sheet: dict, key: tuple[str, int], data: dict) -> dict:
    """브라우저에서 온 한 줄 → CSV 값. 표시는 1/빈칸, 봤으면 reviewed=y."""
    row = dict(sheet.get(key, {}))
    for f, _ in FLAGS:
        row[f] = "1" if data.get(f) else ""
    q = str(data.get("photo_quality") or "").strip()
    row["photo_quality"] = q if q in {k for k, _ in QUALITY} else ""
    tags = data.get("failure_tags")
    tags = {t for t in tags if isinstance(t, str)} if isinstance(tags, list) else set()
    # 손으로 적은 모르는 태그(오타 · 목록에서 뺀 태그)는 화면에 체크박스가 없으니 지우지 않고 뒤에 남긴다
    kept = [t for t in split_tags(row.get("failure_tags")) if t not in TAG_KEYS]
    row["failure_tags"] = ";".join([k for k in TAG_KEYS if k in tags] + kept)
    row["note"] = " ".join(str(data.get("note") or "").split())   # 줄바꿈 없이
    row["reviewed"] = "y" if data.get("reviewed") else ""
    sheet[key] = row
    return row


def page(run_id: str, name: str, items: list[dict], sheet: dict, gt: dict, token: str) -> str:
    # 이미지 주소는 순서 번호라 순서가 바뀌면 같은 주소가 다른 사진이 된다 — 순서로 만든 값을 붙여 옛 캐시를 안 쓰게
    ver = hashlib.sha1(json.dumps([[r["file"], r["repeat"]] for r in items]).encode()).hexdigest()[:10]
    groups: list[list[tuple[int, dict]]] = []      # 붙어 있는 같은 사진 줄 → 카드 한 장
    for i, r in enumerate(items, 1):
        if groups and groups[-1][0][1]["file"] == r["file"]:
            groups[-1].append((i, r))
        else:
            groups.append([(i, r)])
    cards = []
    for k, group in enumerate(groups, 1):
        g = gt.get(group[0][1]["file"], {})
        texts = ", ".join(map(str, g.get("key_texts") or [])) or "—"
        results = []
        for j, (i, r) in enumerate(group):
            s = sheet.get((r["file"], int(r["repeat"])), {})
            checks = "".join(
                f'<label><input type="checkbox" name="{f}"{" checked" if s.get(f) == "1" else ""}> {html.escape(t)}</label>'
                for f, t in FLAGS)
            have = set(split_tags(s.get("failure_tags")))
            tags = "".join(
                f'<label><input type="checkbox" data-tag="{k}"{" checked" if k in have else ""}> {html.escape(t)}</label>'
                for k, t in FAILURE_TAGS)
            quality = "".join(
                f'<label title="{html.escape(t)}"><input type="radio" name="q{i}" value="{k}"'
                f'{" checked" if s.get("photo_quality") == k else ""}> {k}</label>'
                for k, t in QUALITY)
            done = " done" if s.get("reviewed") == "y" else ""
            label = f"결과 {chr(65 + j)}" if len(group) > 1 else "결과"
            results.append(f"""
  <section class="card{done}" data-id="{i}">
    <figure><img loading="lazy" src="/img/{i}/result?v={ver}" alt="{label}"><figcaption>{label} <span class="status"></span></figcaption></figure>
    <div class="form"><b>보존</b><div class="flags">{checks}</div>
      <details{" open" if have else ""}><summary>실패 유형 (바뀐 게 있으면 원인을 골라 주세요)</summary><div class="tags">{tags}</div></details>
      <b>상품 사진 품질</b><div class="quality">{quality}</div>
      <input class="note" type="text" placeholder="메모" value="{html.escape(s.get('note') or '')}">
      <button class="ok">문제 없음 · 저장</button><button class="save">표시한 대로 저장</button>
    </div>
  </section>""")
        cards.append(f"""
<article class="photo">
  <h2>#{k}</h2>
  <p class="meta">지켜야 할 글자: {html.escape(texts)}{(' · ' + html.escape(str(g['note']))) if g.get('note') else ''}</p>
  <div class="pair" style="--n:{len(group) + 1}"><figure class="orig"><img loading="lazy" src="/img/{group[0][0]}/orig?v={ver}" alt="원본"><figcaption>원본</figcaption></figure>{''.join(results)}
  </div>
</article>""")
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>채점 {html.escape(run_id)}</title>
<style>
:root{{--bg:#f6f6f4;--card:#fff;--fg:#222;--muted:#666;--line:#ddd;--done:#e8f4ea;--accent:#2d6cdf}}
@media (prefers-color-scheme:dark){{:root{{--bg:#18181a;--card:#222226;--fg:#eee;--muted:#aaa;--line:#3a3a40;--done:#1f3324;--accent:#7aa7ff}}}}
body{{font:14px system-ui,sans-serif;margin:0;background:var(--bg);color:var(--fg)}}
header{{position:sticky;top:0;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px;z-index:1;display:flex;gap:16px;align-items:center;flex-wrap:wrap}}
main{{padding:16px;max-width:1200px;margin:auto}}
.photo{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:0 0 20px}}
.card{{border-radius:6px;padding:6px}} .card.done{{background:var(--done)}} h2{{font-size:16px;margin:0 0 4px}} .meta{{color:var(--muted);margin:2px 0}}
.pair{{display:grid;grid-template-columns:repeat(var(--n),1fr);gap:12px;margin-top:8px;align-items:start}} figure{{margin:0}} .pair img{{width:100%}}
figcaption{{color:var(--muted);font-size:12px}}
.form{{display:flex;flex-direction:column;gap:6px;margin-top:8px}} .flags,.tags{{display:flex;flex-direction:column;gap:6px}}
.tags{{padding:6px 0 0 8px;font-size:13px}} summary{{cursor:pointer;color:var(--muted)}}
.quality{{display:flex;gap:10px}} .rubric{{font-size:12px;color:var(--muted);flex-basis:100%}}
.note{{padding:6px;border:1px solid var(--line);border-radius:4px;background:var(--card);color:var(--fg)}}
button{{padding:6px 12px;border-radius:4px;border:1px solid var(--accent);background:var(--card);color:var(--accent);cursor:pointer}}
button.ok{{background:var(--accent);color:#fff}} .status{{font-size:12px;color:var(--muted);font-weight:normal}}
@media (max-width:640px){{.pair{{grid-template-columns:1fr}}}}
</style>
<header><b>채점 {html.escape(run_id)} · {html.escape(name)}</b><span id="progress"></span>
<button id="next">다음 안 본 것으로</button>
<span class="rubric">품질 (원본과 비교하지 말고 판매 페이지에 그대로 올릴 만한가): {" · ".join(f"{k} {html.escape(t)}" for k, t in QUALITY)}</span></header>
<main>{''.join(cards)}</main>
<script>
const TOKEN={json.dumps(token)};
const cards=[...document.querySelectorAll('.card')];
function progress(){{const d=cards.filter(c=>c.classList.contains('done')).length;
  document.getElementById('progress').textContent=`${{d}} / ${{cards.length}} 봄`;}}
async function save(card,ok){{
  const boxes=[...card.querySelectorAll('input[type=checkbox]')];
  if(ok&&boxes.some(x=>x.checked)&&!confirm('체크한 표시를 지우고 "문제 없음"으로 저장할까요?'))return;
  const q=card.querySelector('.quality input:checked');
  if(!q&&!confirm('상품 사진 품질 점수 없이 저장할까요?'))return;
  if(ok)boxes.forEach(x=>x.checked=false);
  const body={{id:+card.dataset.id,reviewed:true,
    note:card.querySelector('.note').value}};
  card.querySelectorAll('.flags input').forEach(x=>body[x.name]=x.checked);
  body.failure_tags=[...card.querySelectorAll('.tags input:checked')].map(x=>x.dataset.tag);
  body.photo_quality=q?q.value:'';
  const st=card.querySelector('.status');st.textContent='저장 중…';
  try{{const r=await fetch('/api/save',{{method:'POST',headers:{{'Content-Type':'application/json','X-Grade-Token':TOKEN}},body:JSON.stringify(body)}});
    if(!r.ok)throw new Error(await r.text());
    card.classList.add('done');st.textContent='저장됨';progress();
    const rest=[...card.closest('.photo').querySelectorAll('.card')].filter(c=>!c.classList.contains('done'));
    if(!rest.length){{const nx=card.closest('.photo').nextElementSibling;if(nx)nx.scrollIntoView({{behavior:'smooth'}});}}
  }}catch(e){{st.textContent='저장 실패: '+e.message;}}
}}
cards.forEach(c=>{{c.querySelector('.ok').onclick=()=>save(c,true);c.querySelector('.save').onclick=()=>save(c,false);
  if(c.classList.contains('done'))c.querySelector('.status').textContent='저장됨';}});
document.getElementById('next').onclick=()=>{{const n=cards.find(c=>!c.classList.contains('done'));
  if(n)n.closest('.photo').scrollIntoView({{behavior:'smooth'}});}};
progress();
</script>"""


def make_handler(run_id: str, name: str, rows: list[dict], gt: dict, sheet_path: Path):
    run_dir = (HERE / "results" / "runs" / run_id).resolve()
    items = order(rows, name)
    token = os.urandom(16).hex()   # 다른 웹페이지가 localhost 로 저장 요청을 보내지 못하게

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code: int, body: bytes, ctype: str, cache: str = "no-store") -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path == "/":
                with _LOCK:
                    sheet = read_sheet(sheet_path)
                return self._send(200, page(run_id, name, items, sheet, gt, token).encode(),
                                  "text/html; charset=utf-8")
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[0] == "img" and parts[1].isascii() and parts[1].isdigit() and parts[2] in ("orig", "result"):
                i = int(parts[1])
                if 1 <= i <= len(items):
                    f = (run_dir / items[i - 1][parts[2]]).resolve()
                    if f.is_relative_to(run_dir / "files") and f.is_file():
                        ctype = {".webp": "image/webp", ".png": "image/png"}.get(f.suffix.lower(), "image/jpeg")
                        return self._send(200, f.read_bytes(), ctype, cache="private, max-age=86400")
            self._send(404, b"not found", "text/plain")

        def do_POST(self):
            if self.path != "/api/save":
                return self._send(404, b"not found", "text/plain")
            if (self.headers.get("X-Grade-Token") != token
                    or not (self.headers.get("Content-Type") or "").startswith("application/json")):
                return self._send(403, "페이지를 새로고침하세요".encode(), "text/plain; charset=utf-8")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                if not 0 < n <= MAX_BODY:
                    raise ValueError(n)
                data = json.loads(self.rfile.read(n))
                i = int(data["id"])
                if not 1 <= i <= len(items):
                    raise ValueError(i)
            except (ValueError, KeyError, TypeError):
                return self._send(400, "잘못된 요청".encode(), "text/plain; charset=utf-8")
            key = (items[i - 1]["file"], int(items[i - 1]["repeat"]))
            with _LOCK:
                sheet = read_sheet(sheet_path)
                apply(sheet, key, data)
                write_sheet(sheet_path, rows, sheet)
            self._send(200, b"ok", "text/plain")

    return Handler


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("run_id")
    ap.add_argument("--name", required=True, help="평가자 이름 — results/reviews/<run_id>/<이름>.csv 에 저장")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    if a.name.lower() == "template" or not (a.name.isascii() and a.name.replace("-", "").replace("_", "").isalnum()):
        print("이름은 영문·숫자·-·_ 만 (TEMPLATE 은 안 됨)")
        return 1
    if not (HERE / "results" / "runs" / a.run_id / "results.jsonl").exists():
        print(f"runs/{a.run_id}/results.jsonl 이 없다")
        return 1
    rows = load_rows(a.run_id)
    if not rows:
        print("채점할 이미지가 없다 — 전체 실행(--analyze-only 아님)의 run_id 인가?")
        return 1
    gt = {e["file"]: e for e in json.loads((HERE / "data" / "dataset.json").read_text(encoding="utf-8"))}
    sheet_path = HERE / "results" / "reviews" / a.run_id / f"{a.name}.csv"
    sheet_path.parent.mkdir(parents=True, exist_ok=True)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.run_id, a.name, rows, gt, sheet_path))
    print(f"채점: http://localhost:{a.port} → {sheet_path}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
