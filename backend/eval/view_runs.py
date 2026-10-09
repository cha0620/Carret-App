"""실험 결과 보기 페이지 (10-09) — Codespace 편집기에선 이미지가 안 보여서, 넣은 이미지 · 결과 · 프롬프트를
한 화면에 모아 포트로 띄운다. results/runs/_view/index.html 을 만들고 runs/ 를 127.0.0.1 로 서빙한다.

    cd backend
    python eval/view_runs.py qwen3-1009 coat-noname-flash-1008      # 실행 id 생략 시 최근 3개
    python eval/view_runs.py --no-serve ...                           # 페이지만 만들기

한국어 번역은 runs/<run_id>/prompt_ko.json ({"<post>__<object>": ["1차 번역", "2차 번역"]}) 이 있으면 같이 보인다.
"""
import argparse
import html
import json
import shutil
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "results" / "runs"
IMAGES = HERE / "data" / "images"
SKETCH = HERE / "data" / "refs" / "sketch"
VIEW = RUNS / "_view"

CSS = """body{font-family:sans-serif;margin:16px;background:#f4f4f4;color:#222}
section{background:#fff;padding:16px;margin-bottom:24px;border-radius:8px}h2 small{color:#888;font-weight:normal;font-size:13px}
.row{display:flex;gap:10px;flex-wrap:wrap}figure{margin:0;width:220px}
figure img{width:220px;height:220px;object-fit:contain;background:#eee;border-radius:4px}
figure.main img{outline:3px solid #2a7}figure.bad img{outline:3px solid #c33}figcaption{font-size:13px;text-align:center}
details{margin-top:8px}pre{white-space:pre-wrap;background:#f8f8f8;padding:10px;font-size:13px;border-radius:4px}
.cols{display:flex;gap:16px;flex-wrap:wrap}.cols>div{flex:1;min-width:300px}"""


def _fig(src: str, cap: str, cls: str = "") -> str:
    src, cls = html.escape(src, quote=True), html.escape(cls, quote=True)
    return (f'<figure class="{cls}"><a href="{src}" target="_blank"><img src="{src}" loading="lazy"></a>'
            f"<figcaption>{html.escape(cap)}</figcaption></figure>")


def _input(name: str) -> str:
    """원본 사진 · 선 그림을 _view/in/ 에 복사하고 페이지 기준 경로를 돌려준다 (서빙 범위는 runs/ 뿐)."""
    name = Path(name).name   # 이름만 — posts.json 값으로 다른 폴더 파일을 끌어오지 않게
    for src in (IMAGES / name, SKETCH / name):
        if src.exists():
            dst = VIEW / "in" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                shutil.copy(src, dst)
            return f"in/{name}"
    return ""


def _section(run: str) -> str:
    d = RUNS / Path(run).name
    rows = json.loads((d / "posts.json").read_text(encoding="utf-8"))
    ko_file = d / "prompt_ko.json"
    ko_all = json.loads(ko_file.read_text(encoding="utf-8")) if ko_file.exists() else {}
    out = [f"<h1>{html.escape(run)}</h1>"]
    for r in rows:
        tag = f"{r['post']}__{r['object']}" + (f"_{r['composition']}" if r.get("composition") else "")
        ins = [_fig(_input(r["main"]), "메인 사진", "main")]
        ins += [_fig(_input(x), "다른 각도") for x in r.get("extras") or []]
        ref = r.get("planned_ref") or r.get("style_ref")
        if ref:
            sk = _input(Path(ref).stem + ".png")
            if sk:
                ins.append(_fig(sk, "정답 선 그림"))
        tries = r.get("tries") or []
        res_cls = "bad" if r.get("mode") != "generate" else "main"
        outs = [_fig(f"../{run}/files/{t}", f"{i + 1}차 시도") for i, t in enumerate(tries)]
        outs.append(_fig(f"../{run}/files/{r['result']}", f"최종 ({r.get('mode')}"
                         + (f" · {r['composite_reason']}" if r.get("composite_reason") else "") + ")", res_cls))
        prompts = r.get("gen_prompts") or ([r["prompt_used"]] if r.get("mode") == "generate" else [])
        ko = ko_all.get(tag) or []
        pr = ""
        for i, p in enumerate(prompts or [None]):
            k = ko[i] if i < len(ko) else "(번역 없음 — prompt_ko.json)"
            en = p or "(생성 프롬프트 기록 없음 — 10-09 이전 실행은 배경 교체로 끝나면 덮였다)"
            pr += (f"<details {'open' if i == 0 else ''}><summary>{i + 1}차 프롬프트</summary><div class=cols>"
                   f"<div><b>한국어</b><pre>{html.escape(k)}</pre></div>"
                   f"<div><b>영어 원문</b><pre>{html.escape(en)}</pre></div></div></details>")
        out.append(f"<section><h2>{html.escape(r.get('label') or tag)} <small>{html.escape(tag)} · "
                   f"{html.escape(str(r.get('model')))} · 시도 {r.get('gen_attempts')}회 · {r.get('elapsed_s')}초</small></h2>"
                   f"<h3>넣은 이미지</h3><div class=row>{''.join(ins)}</div>"
                   f"<h3>결과</h3><div class=row>{''.join(outs)}</div>{pr}</section>")
    return "".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--no-serve", action="store_true")
    a = ap.parse_args()
    runs = a.runs or [p.name for p in sorted((p for p in RUNS.iterdir() if (p / "posts.json").exists()),
                                             key=lambda p: p.stat().st_mtime, reverse=True)[:3]]
    VIEW.mkdir(exist_ok=True)
    body = "".join(_section(r) for r in runs)
    (VIEW / "index.html").write_text(f"<!doctype html><meta charset=utf-8><title>실험 결과</title>"
                                     f"<style>{CSS}</style>{body}", encoding="utf-8")
    print(f"페이지: {VIEW / 'index.html'}  ({', '.join(runs)})")
    if not a.no_serve:
        print(f"http://localhost:{a.port}/_view/")
        ThreadingHTTPServer(("127.0.0.1", a.port), partial(SimpleHTTPRequestHandler, directory=str(RUNS))).serve_forever()


if __name__ == "__main__":
    main()
