"""파이프라인 결과 페이지 (10-12) — 실행마다 **넣은 사진(인풋) · 단계별 결과 · 프롬프트 한국어**를 한 화면에.
10-12 사용자: "8001 에서 결과 보여 줄 때 인풋도 같이, 전반적인 파이프라인 결과랑 — 이거 고정으로".
view_runs.py(프롬프트 · 결과만)보다 넓다: 올린 사진 → 실제로 모델에 들어간 사진 → 분석 → 프롬프트 → 시도별 결과 → 검사.

    cd backend
    python eval/view_pipeline.py seedream-1012 picks-gen-1012     # 실행 id 생략 시 최근 3개 · http://localhost:8001
    python eval/view_pipeline.py --no-serve ...                     # 페이지만
(backend 에서 실행 — ⑥ VLM 호출은 Langfuse 를 읽어 runs/<run_id>/vlm_calls.json 에 캐시, app 설정으로 모델 이름을 채운다)

한국어는 무료 기계 번역(view_runs.translate, MyMemory) — 상품 이름 · 읽은 글자가 번역기로 나간다. 번역은
runs/<run_id>/pipeline_ko.json 에 모아 두고 다시 쓰지 않는다. --no-translate 로 끈다. 돈이 드는 호출 없음.
1차 검사 탈락 이유는 파일에 남지 않아 실행 로그에서만 보인다 (gate_retried 만 표시).
"""
import argparse
import html
import json
import shutil
import sqlite3
import time
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import view_runs

HERE = Path(__file__).resolve().parent
RUNS = HERE / "results" / "runs"
IMAGES = HERE / "data" / "images"
SKETCH = HERE / "data" / "refs" / "sketch"
OUT = RUNS / "_pipe"
TRANSLATE = True
GATE = {True: "통과", False: "탈락", None: "검사 안 함 (지킬 마크 0개)"}
SKIP_OBS = {"transform", "item_signals", "generate_image", "dino_similarity", "item_dino_similarity",
            "item_patch_similarity", "transform_dev"}
# VLM 호출마다 들어가는 사진 (프롬프트엔 글만 남아서 — detector.py 의 호출 순서대로, 10-12)
CALL_IMAGES = {
    "objects": "올린 상품 사진 전부 (낮은 해상도)",
    "analyze": "주 사진 1장",
    "read_text": "주 사진 1장",
    "item_text": "주 사진 1장",
    "prep_check": "주 사진 1장 (정답 후보의 준비물이 보이나)",
    "layout_plan": "정답 사진 + 같이 넣은 상품 사진",
    "components": "주 사진 + 다른 각도",
    "check_photo": "생성 결과 1장",
    "verify": "생성 결과 1장 (원본은 마크 목록 글로만)",
    "verify_combined": "원본 + 생성 결과",
    "added_text": "원본 + 생성 결과",
    "count_pieces": "생성 결과 1장",
    "photo_review": "올린 상품 사진",
}


class Ko:
    """실행마다 번역 캐시 (원문 → 한국어)."""
    def __init__(self, run_dir: Path):
        self.f = run_dir / "pipeline_ko.json"
        self.d = json.loads(self.f.read_text(encoding="utf-8")) if self.f.exists() else {}

    def __call__(self, s):
        if not s or not isinstance(s, str):
            return ""
        if s not in self.d:
            if not TRANSLATE:
                return ""
            for wait in (0, 3, 10):   # 초당 요청 한도(MyMemory)에 걸리면 쉬었다 다시
                time.sleep(wait)
                try:
                    self.d[s] = view_runs.translate(s)
                    break
                except Exception as e:   # 번역기가 막혀도 페이지는 만든다
                    err = e
            else:
                print(f"번역 실패: {str(err)[:80]}")
                return ""
            self.f.write_text(json.dumps(self.d, ensure_ascii=False, indent=1), encoding="utf-8")
        return self.d[s]


def _img(src: Path, cap: str, cls: str = "") -> str:
    if not src.exists():
        return ""
    name = f"{src.parent.parent.name}_{src.name}" if src.parent.name == "files" else src.name
    dst = OUT / "img" / name
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists() or dst.stat().st_mtime < src.stat().st_mtime:
        shutil.copy(src, dst)
    return (f'<figure class="{cls}"><a href="img/{html.escape(name)}" target=_blank><img src="img/{html.escape(name)}" '
            f'loading=lazy></a><figcaption>{html.escape(cap)}</figcaption></figure>')


def _row(k: str, v: str, en: str = "") -> str:
    return (f"<tr><th>{html.escape(k)}</th><td>{html.escape(str(v))}"
            + (f"<div class=en>{html.escape(en)}</div>" if en and en != v else "") + "</td></tr>")


def _fed(prompt: str, x: dict) -> set[str]:
    """실제로 모델에 들어간 사진 — 프롬프트가 부르는 번호로 판단 (최소 프롬프트는 다른 각도를 버린다, generation.py)."""
    fed = {x["main"]}
    if "other angle" in prompt or "SAME item from other" in prompt:
        fed |= set(x.get("extras") or [])
    return fed


def _vlm_calls(rd: Path, file_id: str, uploaded_at: str | None) -> list[dict]:
    """이 사진의 VLM 호출 (Langfuse) — 실행 폴더 vlm_calls.json 에 캐시. 변환 trace 는 transform 입력의 file_id 로,
    물건 묶기(objects)는 업로드 때 따로 기록되니 변환 시작 직전의 것으로 찾는다. 못 읽으면 빈 목록."""
    cache_f = rd / "vlm_calls.json"
    cache = json.loads(cache_f.read_text(encoding="utf-8")) if cache_f.exists() else {}
    if file_id in cache:
        return cache[file_id]
    if not uploaded_at:
        return []
    try:
        import datetime as dt
        from dotenv import load_dotenv
        from langfuse import Langfuse
        load_dotenv(HERE.parent / ".env")
        lf = Langfuse()
        t0 = dt.datetime.fromisoformat(uploaded_at).replace(tzinfo=dt.timezone.utc) - dt.timedelta(minutes=1)
        t1 = t0 + dt.timedelta(minutes=30)
        def _fid(o):   # 입력이 dict 이거나 JSON 문자열 (fields=io 로 읽으면 문자열)
            i = o.input
            if isinstance(i, str):
                try:
                    i = json.loads(i)
                except ValueError:
                    return None
            return i.get("file_id") if isinstance(i, dict) else None
        tr = next((o for o in lf.api.observations.get_many(name="transform", from_start_time=t0, to_start_time=t1,
                                                            limit=50, fields="core,basic,io").data
                   if _fid(o) == file_id), None)
        if tr is None:
            return []
        obs = list(lf.api.observations.get_many(trace_id=tr.trace_id, limit=100, fields="core,basic,io").data)
        grp = [o for o in lf.api.observations.get_many(name="objects", from_start_time=t0, to_start_time=tr.start_time,
                                                        limit=20, fields="core,basic,io").data]
        if grp:
            obs.append(max(grp, key=lambda o: o.start_time))
    except Exception as e:   # Langfuse 가 없거나 막혀도 페이지는 만든다
        print(f"Langfuse 못 읽음 ({file_id}): {e}")
        return []
    import sys
    sys.path.insert(0, str(HERE.parent))   # backend — 모델 이름은 앱 설정에서
    from app.core import vlm as _vlm
    calls = []
    for o in sorted(obs, key=lambda o: o.start_time):
        if o.name in SKIP_OBS:
            continue
        out = o.output
        if isinstance(out, str):
            try:
                out = json.loads(out)
            except ValueError:
                pass
        calls.append({"t": o.start_time.strftime("%H:%M:%S"), "name": o.name,
                      "model": o.model or _vlm.model(o.name),
                      "sec": round((o.end_time - o.start_time).total_seconds(), 1) if o.end_time else None,
                      "input": o.input if isinstance(o.input, str) else json.dumps(o.input, ensure_ascii=False),
                      "output": out})
    cache[file_id] = calls
    cache_f.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return calls


def _vlm_section(calls: list[dict], ko: Ko) -> str:
    if not calls:
        return "<h3>⑥ VLM 호출</h3><p class=en>Langfuse 기록을 찾지 못했어요</p>"
    s = ["<h3>⑥ VLM 호출 — 넣은 것 · 받은 것</h3>"]
    for c in calls:
        out = json.dumps(c["output"], ensure_ascii=False, indent=1) if not isinstance(c["output"], str) else c["output"]
        prompt = c.get("input") or ""
        s.append(f"<div class=call><b>{html.escape(c['t'])} · {html.escape(c['name'])}</b> "
                 f"<small>{html.escape(str(c['model']))} · {c['sec']}초 · 사진: {html.escape(CALL_IMAGES.get(c['name'], '—'))}</small>")
        s.append(f"<div class=io><div><div class=lbl>받은 것 (출력)</div><pre>{html.escape(out)}</pre>"
                 f"<div class=lbl>한국어</div><pre>{html.escape(ko(out) or '(번역 안 함 — 위 영어 원문을 봐 주세요)')}</pre></div>")
        s.append(f"<div><details><summary>넣은 것 — 프롬프트 원문 ({len(prompt)}자)</summary><pre>{html.escape(prompt)}</pre></details>"
                 f"<details><summary>프롬프트 한국어</summary><pre>{html.escape(ko(prompt) or '(번역 안 함 — 위 영어 원문을 봐 주세요)')}</pre></details></div></div></div>")
    return "".join(s)


def _case(run: str, rd: Path, x: dict, fid: dict, ko: Ko, uploaded: dict | None = None) -> str:
    f = fid.get(x["main"])
    q = rd / "storage" / "quality"
    an = json.loads((q / f"{f}_analysis.json").read_text(encoding="utf-8")) if f and (q / f"{f}_analysis.json").exists() else {}
    insp = q / f"{f}_studio_white_inspect.json"
    ins = json.loads(insp.read_text(encoding="utf-8")) if f and insp.exists() else {}
    prompts = x.get("gen_prompts") or ([x["prompt_used"]] if x.get("prompt_used") else [])
    fed = _fed(prompts[0] if prompts else "", x)
    sketch = (Path(x["planned_ref"]).stem + ".png") if x.get("planned_ref") else None
    sketch_fed = any("line drawing" in p for p in prompts)

    s = [f"<section><h2>{html.escape(x.get('label') or x['post'])} <small>{html.escape(x['post'])} · {html.escape(run)}</small></h2>"]
    s.append("<h3>① 인풋 — 올린 상품 사진 · 정답 선 그림</h3><div class=row>")
    for p in x.get("all") or [x["main"]]:
        tag = "모델에 들어감" if p in fed else "올렸지만 생성엔 안 들어감"
        s.append(_img(IMAGES / p, f"{p} — {tag}", "fed" if p in fed else "notfed"))
    if sketch:
        s.append(_img(SKETCH / sketch, f"선 그림 {sketch} — {'모델에 들어감' if sketch_fed else '안 들어감'}",
                      "fed" if sketch_fed else "notfed"))
    s.append("</div>")

    s.append("<h3>② 분석</h3><table>")
    s.append(_row("물건", ko(an.get("item")) or an.get("item", ""), an.get("item", "")))
    c = ", ".join(an.get("considered") or [])
    s.append(_row("살펴본 것", ko(c) or c, c))
    for a in an.get("anchors") or []:
        t = f"{a.get('what')} — {a.get('where')} ({a.get('size')})"
        s.append(_row("지킬 마크", ko(t) or t, t))
    if an and not an.get("anchors"):
        s.append(_row("지킬 마크", "없음 → 마크 검사 없이 넘어감"))
    if an:
        s.append(_row("사용감 · 글자 · 개수 · 잘림",
                      f"{an.get('wear_level')} · {an.get('text_level')} · {an.get('item_count')} · {an.get('item_cut_off')}"))
    s.append(_row("정답 구성품 (개수 게이트)", json.dumps(x.get("gt_pieces"), ensure_ascii=False)))
    s.append(_row("모델", x.get("model") or "기본 (fal-ai/flux-2/flash/edit)"))
    s.append("</table>")

    s.append("<h3>③ 생성 프롬프트</h3>")
    for i, p in enumerate(prompts, 1):
        s.append(f"<p><b>{i}차</b> {html.escape(ko(p) or '(번역 없음)')}</p><div class=en>{html.escape(p)}</div>")

    s.append("<h3>④ 시도별 결과</h3><div class=row>")
    for t in x.get("tries") or []:
        s.append(_img(rd / "files" / t, t))
    if x.get("result"):
        s.append(_img(rd / "files" / x["result"], "최종 " + x["result"], "final"))
    s.append("</div>")

    s.append("<h3>⑤ 검사</h3><table>")
    if ins.get("gate_retried"):
        s.append(_row("1차 검사", "탈락 → 1회 재생성 (이유는 실행 로그에만)"))
    for ch in ins.get("checks") or []:
        s.append(_row("마크 보존", f"{ch.get('what')} → {'그대로' if ch.get('preserved') else '사라짐'} (확신 {ch.get('confidence')})"))
    s.append(_row("게이트", GATE.get(ins.get("gate_passed", x.get("gate_passed")), str(ins.get("gate_passed")))))
    s.append(_row("생성 횟수", ins.get("gen_attempts", x.get("gen_attempts", ""))))
    s.append(_row("개수 불일치", json.dumps(x.get("count_mismatch"), ensure_ascii=False)))
    pc = ins.get("photo_check") or {}
    if pc:
        r = pc.get("reason") or ""
        s.append(_row("사진 판정", f"{'괜찮음' if pc.get('valid') else '문제'} — {ko(r) or r}", r))
    if ins.get("visual_similarity") is not None:
        s.append(_row("유사도 (참고)", f"전체 {ins['visual_similarity']:.2f} · 물건 {ins.get('item_similarity') or 0:.2f}"))
    s.append(_row("시간", f"{x.get('elapsed_s')}초"))
    s.append("</table>")
    s.append(_vlm_section(_vlm_calls(rd, f, (uploaded or {}).get(x["main"])) if f else [], ko))
    s.append("</section>")
    return "".join(s)


def _run(run: str) -> str:
    rd = RUNS / run
    pj = rd / "posts.json"
    if not pj.exists():
        return f"<section><h2>{html.escape(run)}</h2><p>posts.json 없음</p></section>"
    fid, uploaded = {}, {}
    if (rd / "carret.db").exists():
        with sqlite3.connect(rd / "carret.db") as db:
            for f, name, at in db.execute("select file_id, original_name, created_at from originals"):
                fid[name], uploaded[name] = f, at
    ko = Ko(rd)
    return "".join(_case(run, rd, x, fid, ko, uploaded) for x in json.loads(pj.read_text(encoding="utf-8")))


CSS = """:root{--bg:#f6f6f6;--card:#fff;--ink:#222;--muted:#888;--line:#eee;--ok:#1a9c53}
@media (prefers-color-scheme:dark){:root{--bg:#16181c;--card:#202329;--ink:#e8e8e8;--muted:#8a8f98;--line:#2c3038}}
body{font-family:sans-serif;margin:0 auto;max-width:1100px;padding:16px;background:var(--bg);color:var(--ink)}
section{background:var(--card);border-radius:8px;padding:16px;margin:16px 0}.row{display:flex;gap:8px;flex-wrap:wrap}
figure{margin:0;width:220px}img{width:220px;height:220px;object-fit:contain;background:#ddd;border-radius:4px}
figcaption{font-size:12px}.fed img{outline:3px solid var(--ok)}.notfed{opacity:.55}.final img{outline:3px solid #ff6a1a}
table{border-collapse:collapse;width:100%}th{text-align:left;width:180px;vertical-align:top;padding:4px;color:var(--muted)}
td{padding:4px}tr{border-bottom:1px solid var(--line)}.en{color:var(--muted);font-size:12px}small{color:var(--muted);font-weight:normal}
.scroll{overflow-x:auto}.cmp td,.cmp th{vertical-align:top;border-bottom:1px solid var(--line);padding:6px}.cmp th{width:auto}
.muted{color:var(--muted);text-align:center}
.call{border-top:1px solid var(--line);padding:8px 0}.io{display:flex;gap:12px;flex-wrap:wrap}.io>div{flex:1;min-width:280px}
pre{white-space:pre-wrap;word-break:break-word;background:var(--bg);padding:8px;font-size:12px;border-radius:4px;max-height:360px;overflow:auto}
.lbl{font-size:12px;color:var(--muted);margin-top:4px}summary{cursor:pointer;font-size:13px}
@media (max-width:600px){figure,img{width:150px;height:150px}th{width:90px}}"""


def _compare(runs: list[str]) -> str:
    """게시글별 한 줄 — 원본 · 모델마다 최종 결과 나란히 (10-12 사용자: "결과들 쫙 보자"). 상세는 아래 실행별.
    같은 모델의 여러 실행은 한 칸으로 — 앞에 준 실행이 이긴다 (새 실행을 앞에 준다)."""
    by_post: dict[str, dict] = {}
    cols: list[str] = []
    for r in runs:
        pj = RUNS / r / "posts.json"
        if not pj.exists():
            continue
        for x in json.loads(pj.read_text(encoding="utf-8")):
            col = (x.get("model") or "fal-ai/flux-2/flash/edit").split("/", 1)[-1]
            if col not in cols:
                cols.append(col)
            d = by_post.setdefault(x["post"], {"label": x.get("label") or x["post"], "main": x["main"], "runs": {}})
            d["runs"].setdefault(col, (r, x))   # 앞에 준 실행이 이긴다 (새 실행을 앞에 — 10-12 옛 결과가 표에 뜬 일)
    if not by_post:
        return ""
    head = "".join(f"<th>{html.escape(c)}</th>" for c in cols)
    rows = []
    for post, d in by_post.items():
        cells = [f"<td>{_img(IMAGES / d['main'], '원본 ' + d['main'])}</td>"]
        for c in cols:
            if c not in d["runs"]:
                cells.append("<td class=muted>—</td>")
                continue
            r, x = d["runs"][c]
            cap = f"{r} · {x.get('mode') or ''} · 게이트 {GATE.get(x.get('gate_passed'), x.get('gate_passed'))} · {x.get('gen_attempts') or 0}회 · {x.get('elapsed_s')}초"
            cells.append(f"<td>{_img(RUNS / r / 'files' / x['result'], cap)}</td>")
        rows.append(f"<tr><th>{html.escape(d['label'])}<br><small>{html.escape(post)}</small></th>{''.join(cells)}</tr>")
    return (f"<section><h2>한눈에 보기 — 게시글별</h2><div class=scroll><table class=cmp><tr><th></th><th>원본</th>{head}</tr>"
            + "".join(rows) + "</table></div></section>")


def build(runs: list[str]) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    body = _compare(runs) + "".join(_run(r) for r in runs)
    page = (f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>파이프라인 결과</title><style>{CSS}</style><h1>파이프라인 결과 — {html.escape(', '.join(runs))}</h1>"
            "<p>초록 테두리 = 실제로 모델에 들어간 사진 · 흐린 것 = 올렸지만 생성엔 안 들어감 · 주황 = 최종 결과. "
            "한국어는 기계 번역, 회색은 원문.</p>" + body)
    (OUT / "index.html").write_text(page, encoding="utf-8")
    return OUT / "index.html"


def main():
    global TRANSLATE
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="*")
    ap.add_argument("--port", type=int, default=8001)
    ap.add_argument("--no-serve", action="store_true")
    ap.add_argument("--no-translate", action="store_true")
    a = ap.parse_args()
    TRANSLATE = not a.no_translate
    runs = a.runs or [p.parent.name for p in sorted(RUNS.glob("*/posts.json"), key=lambda p: p.stat().st_mtime)[-3:][::-1]]
    print(build(runs))
    if not a.no_serve:
        print(f"http://localhost:{a.port}")
        ThreadingHTTPServer(("127.0.0.1", a.port), partial(SimpleHTTPRequestHandler, directory=str(OUT))).serve_forever()


if __name__ == "__main__":
    main()
