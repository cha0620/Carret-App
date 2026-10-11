"""게시글별 구성품 정답 편집 (10-10) — eval/data/posts/<post>/objects.json 을 사진과 같이 보고 고친다.
세트 JSON(종류 · 개수)을 분석(VLM) 대신 미리 저장해 둔 이 정답에서 만들기 위한 것.

    cd backend
    python eval/objects_editor.py            # http://localhost:8002
"""
import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

POSTS = Path(__file__).resolve().parent / "data" / "posts"
ROLES = ("main", "component", "accessory")

PAGE = """<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>구성품 정답</title><style>
body{font-family:sans-serif;margin:0;background:#f4f4f4;color:#222}
nav{position:sticky;top:0;background:#fff;padding:10px 16px;border-bottom:1px solid #ddd;display:flex;gap:6px;flex-wrap:wrap;z-index:2}
nav a{padding:4px 10px;border-radius:12px;background:#eee;text-decoration:none;color:#222;font-size:14px}
nav a.ok{background:#cfe9d4}nav a.on{outline:2px solid #e8641b}
main{max-width:1200px;margin:0 auto;padding:16px}
.photos{display:flex;gap:8px;flex-wrap:wrap}.photos figure{margin:0;width:180px}
.photos img{width:180px;height:180px;object-fit:contain;background:#ddd;border-radius:4px;cursor:zoom-in}
.photos figcaption{font-size:12px;text-align:center}
table{width:100%;min-width:900px;border-collapse:collapse;background:#fff;margin-top:12px;font-size:14px}
td,th{border-bottom:1px solid #eee;padding:6px;vertical-align:top;text-align:left}
input[type=text]{width:100%;box-sizing:border-box}input.n{width:60px}
.chk{display:flex;flex-wrap:wrap;gap:4px}.chk label{font-size:12px;background:#f2f2f2;padding:2px 4px;border-radius:4px}
button{padding:6px 14px;border-radius:6px;border:1px solid #e8641b;background:#fff;color:#e8641b;cursor:pointer}
button.main{background:#e8641b;color:#fff}#msg{margin-left:8px;color:#2a7f2a}
dialog img{max-width:90vw;max-height:85vh}
</style>
<nav id=nav></nav><main id=app></main><dialog id=dlg onclick="this.close()"><img id=big></dialog>
<script>
const ROLES = {main: '본품', component: '구성품', accessory: '부가품'};
let list = [], cur = null, data = null;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c]));
async function load() {
  list = await (await fetch('/api/posts')).json();
  if (!list.length) { document.getElementById('app').textContent = '정답 파일이 없어요'; return; }
  const want = location.hash.slice(1) || list[0].post;
  nav(); show(want);
}
function nav() {
  document.getElementById('nav').innerHTML = list.map(p =>
    `<a href="#${esc(p.post)}" class="${p.checked ? 'ok' : ''} ${p.post === cur ? 'on' : ''}" >${esc(p.name_ko || p.post)}${p.checked ? ' ✓' : ''}</a>`).join('');
}
async function show(post) {
  cur = post; data = await (await fetch('/api/posts/' + post)).json(); nav(); render();
}
function render() {
  const files = data.photos.map(p => p.file);
  const rows = data.objects.map((o, i) => `<tr>
    <td>${esc(o.id)}</td>
    <td><input type=text value="${esc(o.name_ko)}" oninput="data.objects[${i}].name_ko=this.value"><br>
        <input type=text value="${esc(o.what)}" placeholder="영어 이름 (프롬프트에 들어감)" oninput="data.objects[${i}].what=this.value"></td>
    <td><select onchange="data.objects[${i}].role=this.value">${Object.entries(ROLES).map(([k, v]) =>
        `<option value=${k} ${o.role === k ? 'selected' : ''}>${v}</option>`).join('')}</select></td>
    <td><input class=n type=number min=0 value="${o.count}" oninput="data.objects[${i}].count=+this.value"></td>
    <td><input type=checkbox ${o.for_sale ? 'checked' : ''} onchange="data.objects[${i}].for_sale=this.checked"></td>
    <td><div class=chk>${files.map(f => `<label><input type=checkbox ${o.photos.includes(f) ? 'checked' : ''}
        data-f="${esc(f)}" onchange="tog(${i},this.dataset.f,this.checked)">${f.replace(/^.*_p/, 'p').replace('.webp', '')}</label>`).join('')}</div></td>
    <td><input type=text value="${esc(o.note)}" oninput="data.objects[${i}].note=this.value"></td>
    <td><button onclick="data.objects.splice(${i},1);render()">삭제</button></td></tr>`).join('');
  document.getElementById('app').innerHTML = `
    <h2>${esc(data.name_ko)} <small>${esc(data.post)} · ${esc(data.item)}</small></h2>
    <div class=photos>${data.photos.map(p => `<figure><img src="/img/${esc(data.post)}/${esc(p.file)}" onclick="big.src=this.src;dlg.showModal()">
      <figcaption>${p.file.replace(/^.*_p/, 'p')} · ${p.slot === 'proof' ? '근거' : '상품'}</figcaption></figure>`).join('')}</div>
    <div style="overflow-x:auto"><table><tr><th>id</th><th>이름 (한국어 / 영어)</th><th>역할</th><th>개수</th><th>판매</th><th>보이는 사진</th><th>메모</th><th></th></tr>${rows}</table></div>
    <p><button onclick="add()">+ 물건 추가</button></p>
    <p>게시글 메모<br><input type=text value="${esc(data.note)}" oninput="data.note=this.value"></p>
    <p><label><input type=checkbox ${data.checked ? 'checked' : ''} onchange="data.checked=this.checked"> 확인 끝 (정답으로 씀)</label></p>
    <p><button class=main onclick="save()">저장</button><span id=msg></span></p>`;
}
function tog(i, f, on) {
  const ps = data.objects[i].photos;
  if (on && !ps.includes(f)) ps.push(f);
  if (!on) data.objects[i].photos = ps.filter(x => x !== f);
}
function add() {
  const n = Math.max(0, ...data.objects.map(o => +String(o.id).replace(/\\D/g, '') || 0)) + 1;
  data.objects.push({id: 'o' + n, what: '', name_ko: '', role: 'component', count: 1, for_sale: true, photos: [], note: ''});
  render();
}
async function save() {
  const r = await fetch('/api/posts/' + cur, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
  document.getElementById('msg').textContent = r.ok ? '저장했어요' : '저장 실패: ' + await r.text();
  list = await (await fetch('/api/posts')).json(); nav();
}
window.addEventListener('hashchange', () => show(location.hash.slice(1)));
load();
</script>"""


def _post_dir(name: str) -> Path | None:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name or ""):
        return None
    d = POSTS / name
    return d if (d / "objects.json").exists() else None


def _clean(data: dict, old: dict) -> dict:
    """화면에서 온 값 — 고칠 수 있는 칸만 받고 형식을 맞춘다 (사진 목록 · post 는 파일 그대로)."""
    files = {p["file"] for p in old["photos"]}
    objs, ids = [], set()
    for o in data.get("objects") or []:
        oid = str(o.get("id") or "")[:20]
        if not oid or oid in ids:
            oid = next(f"o{k}" for k in range(1, 10_000) if f"o{k}" not in ids and f"o{k}" not in {str(x.get("id")) for x in data["objects"]})
        ids.add(oid)
        objs.append({"id": oid,
                     "what": str(o.get("what") or "").strip()[:80],
                     "name_ko": str(o.get("name_ko") or "").strip()[:80],
                     "role": o.get("role") if o.get("role") in ROLES else "component",
                     "count": max(0, min(999, int(o.get("count") or 0))),
                     "for_sale": o.get("for_sale") is True,
                     "photos": [f for f in o.get("photos") or [] if f in files],
                     "note": str(o.get("note") or "").strip()[:200]})
    return {**old, "objects": objs, "note": str(data.get("note") or "").strip()[:500],
            "checked": data.get("checked") is True}


def _local(host: str) -> bool:
    return host in ("localhost", "127.0.0.1") or host.endswith(".app.github.dev")


class H(BaseHTTPRequestHandler):
    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            return self._send(200, PAGE.encode(), "text/html; charset=utf-8")
        if p == "/api/posts":
            out = []
            for d in sorted(POSTS.iterdir()):
                if _post_dir(d.name):
                    j = json.loads((d / "objects.json").read_text(encoding="utf-8"))
                    out.append({"post": d.name, "name_ko": j.get("name_ko"), "checked": j.get("checked", False)})
            return self._send(200, json.dumps(out, ensure_ascii=False).encode())
        m = re.fullmatch(r"/api/posts/([^/]+)", p)
        if m and (d := _post_dir(m.group(1))):
            return self._send(200, (d / "objects.json").read_bytes())
        m = re.fullmatch(r"/img/([^/]+)/([^/]+)", p)
        if m and (d := _post_dir(m.group(1))):
            j = json.loads((d / "objects.json").read_text(encoding="utf-8"))
            if m.group(2) in {x["file"] for x in j["photos"]}:
                return self._send(200, (d / m.group(2)).read_bytes(), "image/webp")
        self._send(404, b"not found", "text/plain")

    def do_POST(self):
        m = re.fullmatch(r"/api/posts/([^/]+)", self.path.split("?")[0])
        d = _post_dir(m.group(1)) if m else None
        if d is None:
            return self._send(404, b"not found", "text/plain")
        # 다른 사이트 페이지가 브라우저로 덮어쓰지 못하게 (10-10 리뷰): JSON 만 · 이 서버 주소에서 온 것만
        # Codespace 포트 전달로 열면 Host 가 *.app.github.dev 다 — 그것도 허용, Origin 은 Host 와 같아야
        host = (self.headers.get("Host") or "").split(":")[0]
        origin = self.headers.get("Origin")
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json" or not _local(host) \
                or (origin and re.sub(r"^https?://([^:/]+).*$", r"\1", origin) != host):
            return self._send(403, b"forbidden", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
            if not 0 < n <= 1_000_000:
                raise ValueError("본문 크기")
            data = json.loads(self.rfile.read(n))
            if not isinstance(data, dict) or not all(isinstance(o, dict) for o in data.get("objects") or []):
                raise ValueError("형식")
            old = json.loads((d / "objects.json").read_text(encoding="utf-8"))
            new = _clean(data, old)
        except (ValueError, TypeError) as e:
            return self._send(400, str(e).encode(), "text/plain; charset=utf-8")
        tmp = d / "objects.json.tmp"   # 쓰다 실패해도 정답이 깨지지 않게
        tmp.write_text(json.dumps(new, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        tmp.replace(d / "objects.json")
        self._send(200, b"{}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8002)
    a = ap.parse_args()
    print(f"http://localhost:{a.port}/")
    ThreadingHTTPServer(("127.0.0.1", a.port), H).serve_forever()


if __name__ == "__main__":
    main()
