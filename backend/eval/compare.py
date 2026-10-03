"""두 실행 비교 — 기준(base)과 실험(exp)을 한 페이지에: 숫자표 · 바뀐 조건 · 잠금 문구 차이 · 사진 나란히.

    cd backend
    python eval/compare.py 20261001-lock-trim 20261003-lock-sheen
    python eval/compare.py <base> <exp> --rater cha0620     # 이 평가자 채점만 (기본: 모든 평가자 다수결)

나오는 것:
  runs/compare-<base>-vs-<exp>.html   브라우저로 (runs/ 를 띄운 서버에서 열면 사진이 보인다)
  stdout                              숫자표 (markdown) — EXPERIMENTS.md 에 붙인다

사람 판정은 report.py 와 같은 규칙 (물건 표시 없으면 보존 통과, 평가자 여럿이면 다수결 · 동점은 실패).
두 실행의 사진 · repeat · 데이터셋 · 평가자가 다르거나 기록이 없으면 맨 위에 경고 — 숫자를 그대로 비교하면 안 된다.
"""
import argparse
import difflib
import html
import json
import re
import statistics
import sys
from urllib.parse import quote
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import report as rp  # noqa: E402

# 결과를 바꾸는 조건 — 다르면 표에 나온다 (run.py meta.json)
CONDITION_KEYS = ("gen_model", "gen_steps", "vlm_model", "preset", "lock_sha", "lock_file", "commit",
                  "app_dirty", "app_diff_sha", "repeat", "set", "split", "only", "note")


# ── 읽기 ────────────────────────────────────────
def load_run(run_id: str) -> tuple[dict, list[dict]]:
    run_dir = HERE / "runs" / run_id
    if not (run_dir / "results.jsonl").exists():
        raise SystemExit(f"runs/{run_id}/results.jsonl 이 없다 — 끝까지 안 돈 실행인지 확인")
    meta_path = run_dir / "meta.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    results = [json.loads(line) for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines()
               if line.strip()]
    return meta, results


# ── 계산 (순수 함수) ─────────────────────────────
def raters_of(reviews: dict, rater: str | None = None) -> list[str]:
    return [rater] if rater else sorted(reviews)


def verdicts(results: list[dict], reviews: dict, rater: str | None = None) -> dict:
    """{(file, repeat): {"preserved", "clean", "quality", "tags", "n"}} — report.build_report 와 같은 합치기."""
    raters = raters_of(reviews, rater)
    out = {}
    for r in results:
        if r.get("error"):
            continue
        key = (r["file"], r["repeat"])
        vs = [reviews[p][key] for p in raters if p in reviews and key in reviews[p]]
        if vs:
            out[key] = {"preserved": rp.majority([v["preserved"] for v in vs]),
                        "clean": rp.majority([v["clean"] for v in vs]),
                        "quality": rp.mean([v["quality"] for v in vs if v.get("quality") is not None]),
                        "tags": sorted({t for v in vs for t in v.get("tags", ())}), "n": len(vs)}
    return out


def summarize(results: list[dict], verdict: dict) -> dict:
    ok = [r for r in results if not r.get("error")]
    gen = [r for r in ok if r.get("mode") == "generate"]
    gated = [r for r in gen if r.get("gate_passed") is not None]
    elapsed = [r["elapsed_s"] for r in ok if isinstance(r.get("elapsed_s"), (int, float))]
    rated = [v for v in verdict.values() if v["quality"] is not None]
    reps = defaultdict(list)                 # 사진 → 오류 없는 회차들
    for r in ok:
        reps[r["file"]].append(r["repeat"])
    # 물건 기준은 모든 회차가 채점된 사진만 (r1 만 채점된 사진을 "매번 통과"로 치지 않게)
    per_item = {f: [verdict[(f, k)]["preserved"] for k in ks] for f, ks in reps.items()
                if all((f, k) in verdict for k in ks)}
    return {
        "runs": len(results), "errors": len(results) - len(ok),
        "modes": Counter(r.get("mode") for r in ok),
        "gate": (sum(r["gate_passed"] is True for r in gated), len(gated)),
        "trust": rp.mean([x for r in gen if (x := rp.metric_value(r, "judge.trust")) is not None]),
        "item_dino": rp.mean([x for r in gen if (x := rp.metric_value(r, "item_similarity")) is not None]),
        "elapsed": statistics.median(elapsed) if elapsed else None,
        "reviewed": (len(verdict), len(ok)),
        "preserved": (sum(v["preserved"] for v in verdict.values()), len(verdict)),
        "clean": (sum(v["clean"] for v in verdict.values()), len(verdict)),
        "usable": (sum(v["preserved"] and v["quality"] >= rp.USABLE_QUALITY for v in rated), len(rated)),
        "quality": rp.mean([v["quality"] for v in rated]),
        "every_item": (sum(all(v) for v in per_item.values()), len(per_item)),
    }


MISSING = object()   # meta.json 에 칸이 없음 (옛 실행) — None 값과 구분


def _meta(m: dict, k: str):
    return m.get(k, MISSING)


def warnings(mb: dict, me: dict, rb: list[dict], re_: list[dict], vb: dict, ve: dict,
             raters_b: list[str] = (), raters_e: list[str] = ()) -> list[str]:
    """숫자를 그대로 비교하면 안 되는 이유들 — 값이 다를 때도, 한쪽이라도 기록이 없을 때도."""
    out = []
    for name, m in (("기준", mb), ("실험", me)):
        if m.get("full") is False:
            out.append(f"{name} 은 --analyze-only 실행 — 생성 결과 · 사람 채점이 없다")
    fb, fe = {r["file"] for r in rb}, {r["file"] for r in re_}
    if fb != fe:
        only_b, only_e = sorted(fb - fe), sorted(fe - fb)
        out.append("사진이 다르다 — " + " · ".join(
            x for x in (f"기준에만 {', '.join(only_b)}" if only_b else "",
                        f"실험에만 {', '.join(only_e)}" if only_e else "") if x))
    rb_, re_r = _meta(mb, "repeat"), _meta(me, "repeat")
    if MISSING in (rb_, re_r):
        out.append("repeat 기록이 없는 실행이 있다 (meta.json)")
    elif rb_ != re_r:
        out.append(f"repeat 이 다르다 ({rb_} vs {re_r})")
    db, de = _meta(mb, "dataset_sha"), _meta(me, "dataset_sha")
    if MISSING in (db, de):
        out.append("데이터셋 버전(dataset_sha) 기록이 없는 실행이 있다 — 같은 라벨로 돌렸는지 모른다")
    elif db != de:
        out.append("데이터셋 버전(dataset_sha)이 다르다 — 라벨이나 사진이 바뀌었다")
    if list(raters_b) != list(raters_e) and (vb or ve):
        out.append(f"평가자가 다르다 (기준 {', '.join(raters_b) or '없음'} · 실험 {', '.join(raters_e) or '없음'})"
                   " — 같은 잣대로 채점되지 않았다 (--rater 로 한 명만 보기)")
    for name, results, v in (("기준", rb, vb), ("실험", re_, ve)):
        n_ok = sum(1 for r in results if not r.get("error"))
        if len(v) < n_ok:
            out.append(f"{name} 사람 채점 {len(v)}/{n_ok} — 채점이 덜 됐다")
    return out


def condition_diff(mb: dict, me: dict) -> list[tuple[str, object, object]]:
    """바뀐 조건 — 한쪽에 칸이 없으면 값 대신 "(기록 없음)" (옛 실행을 "모델이 바뀜"으로 읽지 않게)."""
    def show(v):
        return "(기록 없음)" if v is MISSING else v
    return [(k, show(_meta(mb, k)), show(_meta(me, k))) for k in CONDITION_KEYS
            if _meta(mb, k) != _meta(me, k)]


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+", " ".join(str(text or "").split())) if s]


def lock_diff(a: str, b: str) -> list[tuple[str, str]]:
    """문장 단위 차이 [(" " | "-" | "+", 문장)]."""
    return [(d[0], d[2:]) for d in difflib.ndiff(sentences(a), sentences(b)) if d[0] in " -+"]


def file_order(files: list[str], vb: dict, ve: dict) -> list[str]:
    """나빠진 사진 → 좋아진 사진 → 같음 (보존 통과 비율 기준). 한쪽이라도 채점이 없으면 맨 뒤."""
    def passed(v, f):
        ks = [k for k in v if k[0] == f]
        return sum(v[k]["preserved"] for k in ks) / len(ks) if ks else None

    def rank(f):
        b, e = passed(vb, f), passed(ve, f)
        if b is None or e is None:
            return (3, f)
        return (0 if e < b else 1 if e > b else 2, f)
    return sorted(files, key=rank)


# ── 출력 ────────────────────────────────────────
def _frac(t: tuple[int, int]) -> str:
    return rp.pct(*t)


def _sec(v) -> str:
    return "—" if v is None else f"{v:.1f}s"


def _code(s: str) -> str:
    """markdown 표 칸 안의 run_id — | 와 백틱이 표를 깨지 않게."""
    return "`" + s.replace("`", "'").replace("|", "/") + "`"


def summary_table(base: str, exp: str, sb: dict, se: dict) -> str:
    def modes(s):
        return " · ".join(f"{m} {s['modes'].get(m, 0)}" for m in ("generate", "composite", "original"))
    rows = [
        ("실행 (오류)", f"{sb['runs']} ({sb['errors']})", f"{se['runs']} ({se['errors']})"),
        ("경로", modes(sb), modes(se)),
        ("**보존 통과** (사람)", _frac(sb["preserved"]), _frac(se["preserved"])),
        ("결함 없음 (사람)", _frac(sb["clean"]), _frac(se["clean"])),
        ("바로 쓸 수 있음 (사람)", _frac(sb["usable"]), _frac(se["usable"])),
        ("품질 평균 (1~5)", rp.fmt(sb["quality"]), rp.fmt(se["quality"])),
        ("물건 기준 매번 통과 (모든 회차 채점된 사진)", _frac(sb["every_item"]), _frac(se["every_item"])),
        ("사람 채점", _frac(sb["reviewed"]), _frac(se["reviewed"])),
        ("게이트 통과 (생성본)", _frac(sb["gate"]), _frac(se["gate"])),
        ("judge trust 평균", rp.fmt(sb["trust"]), rp.fmt(se["trust"])),
        ("item_dino 평균", rp.fmt(sb["item_dino"], 3), rp.fmt(se["item_dino"], 3)),
        ("소요 시간 중앙값", _sec(sb["elapsed"]), _sec(se["elapsed"])),
    ]
    lines = [f"| | 기준 {_code(base)} | 실험 {_code(exp)} |", "|---|---|---|"]
    lines += [f"| {a} | {b} | {c} |" for a, b, c in rows]
    return "\n".join(lines)


def _md_to_html_table(md: str) -> str:
    lines = [ln for ln in md.splitlines() if not ln.startswith("|---")]
    out = ["<table>"]
    for i, ln in enumerate(lines):
        cells = [c.strip() for c in ln.strip("|").split("|")]
        tag = "th" if i == 0 else "td"
        cells = [re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", re.sub(r"`(.+?)`", r"<code>\1</code>", html.escape(c)))
                 for c in cells]
        out.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
    return "\n".join(out + ["</table>"])


def _badge(r: dict | None, v: dict | None) -> str:
    if r is None:
        return '<div class="badge none">없음</div>'
    if r.get("error"):
        return f'<div class="badge bad">오류 {html.escape(str(r["error"])[:60])}</div>'
    mode = html.escape(str(r.get("mode") or "?"))
    if v is None:
        return f'<div class="badge none">{mode} · 채점 전</div>'
    q = "" if v["quality"] is None else f" · 품질 {v['quality']:.1f}"
    tags = f'<div class="tags">{html.escape(", ".join(v["tags"]))}</div>' if v["tags"] else ""
    cls, word = ("good", "보존 ✓") if v["preserved"] else ("bad", "보존 ✗")
    return f'<div class="badge {cls}">{mode} · {word}{q}</div>{tags}'


def src(run_id: str, rel) -> str | None:
    """runs/ 서버 기준 이미지 주소 — 상대 경로만 (절대경로 · .. 은 None), URL 인코딩 + HTML 이스케이프."""
    rel = str(rel or "")
    if not rel or rel.startswith(("/", "\\")) or ".." in Path(rel).parts:
        return None
    return html.escape(quote(f"{run_id}/{rel}"), quote=True)


def _cell(run_id: str, r: dict | None, v: dict | None, label: str = "") -> str:
    img = ""
    s = src(run_id, r.get("result")) if r and not r.get("error") else None
    if s:
        img = f'<a href="{s}"><img src="{s}" alt="결과" loading="lazy"></a>'
    cap = f'<div class="who">{html.escape(label)}</div>' if label else ""
    return f"<figure>{cap}{img}{_badge(r, v)}</figure>"


def build_html(base: str, exp: str, mb: dict, me: dict, rb: list[dict], re_: list[dict],
               vb: dict, ve: dict, rater: str | None, raters_b: list[str] = (), raters_e: list[str] = ()) -> str:
    sb, se = summarize(rb, vb), summarize(re_, ve)
    esc = html.escape
    warn = warnings(mb, me, rb, re_, vb, ve, raters_b, raters_e)
    parts = [f"<h1>{esc(base)} → {esc(exp)}</h1>",
             f'<p class="muted">사람 판정: {esc(rater) if rater else "모든 평가자 다수결 (동점은 실패)"}</p>']
    if warn:
        parts.append('<section class="warn"><h2>⚠ 그대로 비교하면 안 되는 점</h2><ul>'
                     + "".join(f"<li>{esc(w)}</li>" for w in warn) + "</ul></section>")
    parts.append("<section><h2>숫자</h2>" + _md_to_html_table(summary_table(base, exp, sb, se)) + "</section>")
    cd = condition_diff(mb, me)
    parts.append("<section><h2>바뀐 조건</h2>" + ("<table><tr><th></th><th>기준</th><th>실험</th></tr>" + "".join(
        f"<tr><td>{esc(k)}</td><td>{esc(str(a))}</td><td>{esc(str(b))}</td></tr>" for k, a, b in cd) + "</table>"
        if cd else '<p class="muted">meta.json 조건이 같다</p>') + "</section>")
    lb, le = _meta(mb, "lock"), _meta(me, "lock")
    ld = lock_diff(lb, le) if MISSING not in (lb, le) else []
    if MISSING in (lb, le):
        who = " · ".join(n for n, v in (("기준", lb), ("실험", le)) if v is MISSING)
        body = f'<p class="muted">{esc(who)} 실행에 잠금 문구 기록이 없다 — 비교할 수 없다</p>'
    elif any(op != " " for op, _ in ld):
        body = "".join(f'<p class="{ {"-": "old", "+": "new", " ": "same"}[op]}">{op} {esc(s)}</p>' for op, s in ld)
    else:
        body = '<p class="muted">잠금 문구가 같다</p>'
    parts.append(f"<section><h2>잠금 문구 차이 (문장 단위)</h2>{body}</section>")

    by_b = {(r["file"], r["repeat"]): r for r in rb}
    by_e = {(r["file"], r["repeat"]): r for r in re_}
    files = file_order(sorted({r["file"] for r in rb} | {r["file"] for r in re_}), vb, ve)
    reps = max([r["repeat"] for r in rb + re_] or [1])
    origs = {}                                  # 사진 → 원본 주소 (기준 쪽 먼저)
    for run_id, rows in ((exp, re_), (base, rb)):
        for r in rows:
            s = src(run_id, r.get("orig"))
            if s:
                origs[r["file"]] = s
    parts.append("<section><h2>사진별 (나빠진 것 → 좋아진 것 → 같음 → 채점 덜 됨)</h2>")
    for f in files:
        orig_img = f'<img src="{origs[f]}" alt="원본" loading="lazy">' if f in origs else ""
        parts.append(f'<div class="row"><h3>{esc(f)}</h3>'
                     f'<div class="grid" style="--n:{reps + 1}">'
                     f'<figure><div class="who">원본</div>{orig_img}</figure>'
                     + "".join(_cell(base, by_b.get((f, k)), vb.get((f, k)), f"기준 r{k}") for k in range(1, reps + 1))
                     + '</div><div class="grid exp" style="--n:' + str(reps + 1) + '"><figure></figure>'
                     + "".join(_cell(exp, by_e.get((f, k)), ve.get((f, k)), f"실험 r{k}") for k in range(1, reps + 1))
                     + "</div></div>")
    parts.append("</section>")
    return f"""<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>비교 {esc(base)} vs {esc(exp)}</title>
<style>
:root{{--bg:#f6f6f4;--card:#fff;--fg:#222;--muted:#666;--line:#ddd;--old:#fbeaea;--new:#e8f4ea;--warn:#fff4dc;--good:#2e7d32;--bad:#c62828}}
@media (prefers-color-scheme:dark){{:root{{--bg:#18181a;--card:#222226;--fg:#eee;--muted:#aaa;--line:#3a3a40;--old:#3a2224;--new:#1f3324;--warn:#3a3020;--good:#81c784;--bad:#ef9a9a}}}}
body{{font:14px system-ui,sans-serif;margin:0;background:var(--bg);color:var(--fg)}} main{{padding:16px;max-width:1500px;margin:auto}}
section{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:12px 16px;margin:0 0 20px}}
section.warn{{background:var(--warn)}} h1{{font-size:18px}} h2{{font-size:16px;margin:0 0 8px}} h3{{font-size:13px;margin:12px 0 6px}}
table{{border-collapse:collapse}} td,th{{border:1px solid var(--line);padding:4px 8px;text-align:left;vertical-align:top}}
.muted{{color:var(--muted)}} .old{{background:var(--old)}} .new{{background:var(--new)}} .same{{color:var(--muted)}}
p.old,p.new,p.same{{margin:2px 0;padding:2px 6px;border-radius:4px}}
.row{{overflow-x:auto}} .grid{{display:grid;grid-template-columns:repeat(var(--n),minmax(140px,1fr));gap:8px;align-items:start;min-width:calc(var(--n)*148px)}}
.grid.exp{{margin-top:6px;padding-top:6px;border-top:2px dashed var(--line)}} .who{{font-size:12px;font-weight:600;margin-bottom:2px}} figure{{margin:0}} img{{width:100%;border-radius:6px;border:1px solid var(--line);background:#fff}}
.badge{{font-size:12px;margin-top:4px}} .badge.good{{color:var(--good)}} .badge.bad{{color:var(--bad)}} .badge.none{{color:var(--muted)}}
.tags{{font-size:11px;color:var(--muted)}}
</style>
<main>
<p class="muted">사진마다 윗줄: 원본 · 기준 r1…r{reps}, 아랫줄: 실험 r1…r{reps}</p>
{"".join(parts)}
</main>"""


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("base")
    ap.add_argument("exp")
    ap.add_argument("--rater", default="", help="이 평가자 채점만 (reviews/<run_id>/<이름>.csv)")
    a = ap.parse_args(argv)
    mb, rb = load_run(a.base)
    me, re_ = load_run(a.exp)
    rv_b, rv_e = rp.load_reviews(a.base), rp.load_reviews(a.exp)
    rater = a.rater or None
    if rater and rater not in rv_b and rater not in rv_e:
        known = sorted(set(rv_b) | set(rv_e))
        raise SystemExit(f"평가자 {rater!r} 의 채점이 두 실행 어디에도 없다 (있는 평가자: {', '.join(known) or '없음'})")
    raters_b, raters_e = raters_of(rv_b, rater), raters_of(rv_e, rater)
    vb, ve = verdicts(rb, rv_b, rater), verdicts(re_, rv_e, rater)
    out = HERE / "runs" / f"compare-{a.base}-vs-{a.exp}.html"
    out.write_text(build_html(a.base, a.exp, mb, me, rb, re_, vb, ve, rater, raters_b, raters_e), encoding="utf-8")
    for w in warnings(mb, me, rb, re_, vb, ve, raters_b, raters_e):
        print(f"⚠ {w}")
    print(summary_table(a.base, a.exp, summarize(rb, vb), summarize(re_, ve)))
    for k, b, e in condition_diff(mb, me):
        print(f"- {k}: {b} → {e}")
    print(f"\n페이지: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
