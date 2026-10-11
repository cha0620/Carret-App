"""실험 결과 보기 페이지 (10-09) — Codespace 편집기에선 이미지가 안 보여서, 넣은 이미지 · 결과 · 프롬프트를
한 화면에 모아 포트로 띄운다. results/runs/_view/index.html 을 만들고 runs/ 를 127.0.0.1 로 서빙한다.

    cd backend
    python eval/view_runs.py qwen3-1009 coat-noname-flash-1008      # 실행 id 생략 시 최근 3개
    python eval/view_runs.py --no-serve ...                           # 페이지만 만들기

한국어 번역은 runs/<run_id>/prompt_ko.json ({"<post>__<object>": ["1차 번역", "2차 번역"]}) 이 있으면 같이 보인다.
없는 번역은 무료 번역기(MyMemory, deep-translator)로 채워 prompt_ko.json 에 저장한다 (10-10). --no-translate 로 끈다.
JSON 프롬프트는 키는 두고 값만 옮긴다. 프롬프트(상품 이름 · 읽은 글자 포함)가 MyMemory 로 나간다 — 싫으면 --no-translate.
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
TRANSLATE = True


def _mt(text: str) -> str:
    """영어 → 한국어 한 덩어리. MyMemory 는 500자 제한이라 문단 · 문장으로 나눠 보낸다."""
    from deep_translator import MyMemoryTranslator
    tr = MyMemoryTranslator(source="en-US", target="ko-KR")
    out = []
    for para in text.split("\n"):
        chunks, cur = [], ""
        for sent in para.replace(". ", ".\x00").split("\x00"):
            if len(cur) + len(sent) > 450 and cur:
                chunks.append(cur); cur = ""
            cur += sent
        chunks.append(cur)
        got = []
        for c in chunks:
            if not c.strip():
                got.append(c); continue
            t = tr.translate(c[:490]) or ""
            # 한도를 넘으면 예외 대신 경고 문장을 번역인 척 돌려준다 — 저장되면 다시 번역하지 않는다 (10-10 리뷰)
            if "MYMEMORY WARNING" in t or "QUERY LENGTH LIMIT" in t or not t:
                raise RuntimeError(t[:80] or "빈 번역")
            got.append(t + (" …" if len(c) > 490 else ""))
        out.append(" ".join(got))
    return "\n".join(out)


def translate(prompt: str) -> str:
    """생성 프롬프트 → 한국어 (기계 번역 — 뜻만 보는 용도). JSON 블록은 값만 옮긴다."""
    blocks = []
    for block in prompt.split("\n\n"):
        try:
            data = json.loads(block)
        except ValueError:
            blocks.append(_mt(block)); continue
        def walk(v):
            if isinstance(v, dict):
                return {k: walk(x) for k, x in v.items()}
            return _mt(v) if isinstance(v, str) else v
        blocks.append(json.dumps(walk(data), ensure_ascii=False, indent=1))
    return "\n\n".join(blocks)

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
        if TRANSLATE and prompts and len(ko) < len(prompts):
            for p in prompts[len(ko):]:
                try:
                    ko = ko + [translate(p)]
                except Exception as e:   # 번역기가 막혀도 페이지는 만든다 — 앞에서 된 것까지만 저장
                    print(f"번역 실패 {tag}: {e}")
                    break
            if ko:
                ko_all[tag] = ko
                ko_file.write_text(json.dumps(ko_all, ensure_ascii=False, indent=1), encoding="utf-8")
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
    ap.add_argument("--no-translate", action="store_true", help="없는 번역을 번역기로 채우지 않는다")
    a = ap.parse_args()
    global TRANSLATE
    TRANSLATE = not a.no_translate
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
