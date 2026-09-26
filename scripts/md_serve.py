"""레포의 .md 파일을 브라우저로 보는 간단한 뷰어 서버.

사용: python scripts/md_serve.py [--port 8090] [--root .]
- 디렉터리 → .md 파일/하위 폴더 목록
- *.md → marked + mermaid 로 렌더링 (?raw=1 이면 원문)
"""
import argparse
import html
import json
import os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, parse_qs

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache"}

PAGE = """<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/github-markdown-css@5/github-markdown.min.css">
<style>
  body {{ margin:0; background:#fff; }}
  @media (prefers-color-scheme: dark) {{ body {{ background:#0d1117; }} }}
  .markdown-body {{ box-sizing:border-box; max-width:980px; margin:0 auto; padding:32px 16px; }}
  .crumbs {{ font-size:14px; margin-bottom:16px; }}
</style></head>
<body><article class="markdown-body">
<div class="crumbs">{crumbs}</div>
<div id="content">{body}</div>
</article>
{script}
</body></html>"""

RENDER_SCRIPT = """<script src="https://cdn.jsdelivr.net/npm/marked@12/marked.min.js"></script>
<script type="module">
import mermaid from "https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.esm.min.mjs";
const src = %s;
const el = document.getElementById("content");
el.innerHTML = marked.parse(src);
el.querySelectorAll("pre > code.language-mermaid").forEach(code => {
  const div = document.createElement("div");
  div.className = "mermaid";
  div.textContent = code.textContent;
  code.parentElement.replaceWith(div);
});
const dark = matchMedia("(prefers-color-scheme: dark)").matches;
mermaid.initialize({ startOnLoad: false, theme: dark ? "dark" : "default" });
await mermaid.run();
</script>"""


def crumbs(rel: str) -> str:
    parts = [p for p in rel.split("/") if p]
    out = ['<a href="/">root</a>']
    for i, p in enumerate(parts):
        href = "/" + "/".join(quote(x) for x in parts[: i + 1])
        out.append(f'<a href="{href}">{html.escape(p)}</a>')
    return " / ".join(out)


def has_md(d: Path) -> bool:
    for dirpath, dirnames, filenames in os.walk(d):
        dirnames[:] = [x for x in dirnames if x not in SKIP_DIRS and not x.startswith("venv")]
        if any(f.lower().endswith(".md") for f in filenames):
            return True
    return False


class Handler(SimpleHTTPRequestHandler):
    def do_GET(self):
        url = urlsplit(self.path)
        rel = unquote(url.path).lstrip("/")
        root = Path(self.directory).resolve()
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            return self.send_error(403)
        if target.is_dir():
            return self._send_html(self._listing(root, target, rel))
        if target.suffix.lower() == ".md" and target.is_file():
            text = target.read_text(encoding="utf-8", errors="replace")
            if "raw" in parse_qs(url.query):
                return self._send(text.encode(), "text/plain; charset=utf-8")
            # </script> 조기 종료 방지
            src = json.dumps(text).replace("</", "<\\/")
            page = PAGE.format(
                title=html.escape(target.name),
                crumbs=crumbs(rel) + f' · <a href="?raw=1">raw</a>',
                body="",
                script=RENDER_SCRIPT % src,
            )
            return self._send_html(page)
        return super().do_GET()  # 이미지 등 상대경로 자원

    def _listing(self, root: Path, d: Path, rel: str) -> str:
        items = []
        for p in sorted(d.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
            if p.name in SKIP_DIRS or p.name.startswith("venv"):
                continue
            href = "/" + quote(str(p.relative_to(root)))
            if p.is_dir() and has_md(p):
                items.append(f'<li>📁 <a href="{href}/">{html.escape(p.name)}/</a></li>')
            elif p.is_file() and p.suffix.lower() == ".md":
                items.append(f'<li>📄 <a href="{href}">{html.escape(p.name)}</a></li>')
        body = "<ul>" + "".join(items) + "</ul>" if items else "<p>(.md 없음)</p>"
        return PAGE.format(title=html.escape(rel or "docs"), crumbs=crumbs(rel), body=body, script="")

    def _send_html(self, page: str):
        self._send(page.encode(), "text/html; charset=utf-8")

    def _send(self, data: bytes, ctype: str):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=int(os.environ.get("MD_PORT", 8090)))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--root", default=str(Path(__file__).resolve().parent.parent))
    a = ap.parse_args()
    handler = lambda *args, **kw: Handler(*args, directory=a.root, **kw)
    print(f"md viewer: http://localhost:{a.port}  (root={a.root})")
    ThreadingHTTPServer((a.host, a.port), handler).serve_forever()


if __name__ == "__main__":
    main()
