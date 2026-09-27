"""평가 집계 — runs/<run_id>/results.jsonl + dataset.json(정답) + reviews/<run_id>/*.csv(사람 채점).

    cd backend
    python eval/report.py                # 가장 최근 실행
    python eval/report.py 20260927-2300-full

나오는 것 (runs/<run_id>/report.md 에도 저장):
  1. 분석 정확도 — photo_type · wear_level · text_level 이 정답과 맞나 (+ 틀린 사진)
  2. 경로 분포 — 정답 사진 종류별로 생성 / 배경 교체 / 원본 그대로
  3. 사람 판정 — 보존 통과율(물건 변화 없음), 결함 없음 비율, 사진 기준 · 물건 기준(repeat>1), 실패 유형
  4. 평가자 일치도 — Cohen's kappa (평가자 2명 이상)
  5. 자동 지표 vs 사람 — judge·DINO·게이트가 사람 판정을 얼마나 맞히나 (AUC · 상관)

판정 규칙 (README): 물건 쪽 표시(형태·색 / 글자 / 하자) 가 하나도 없으면 "보존 통과",
표시가 하나도 없으면 "결함 없음". 평가자가 여럿이면 다수결, 동점은 실패로 (보수적으로).
"""
import csv
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OBJECT_FLAGS = ("shape_color_changed", "text_changed", "wear_changed")
QUALITY_FLAGS = ("background_issue", "framing_issue")
AUTO_METRICS = ("judge.fidelity", "judge.trust", "visual_similarity", "item_similarity",
                "item_patch_similarity", "gate_passed")


# ── 계산 (순수 함수) ─────────────────────────────
def flagged(v) -> bool:
    return str(v or "").strip().lower() in ("1", "y", "yes", "x", "o", "true")


def rater_verdict(row: dict) -> dict | None:
    """평가자 한 명의 한 줄 → {"preserved", "clean", "flags"}. reviewed 가 비어 있으면 None(안 봄)."""
    if not flagged(row.get("reviewed")):
        return None
    flags = [f for f in OBJECT_FLAGS + QUALITY_FLAGS if flagged(row.get(f))]
    return {"preserved": not any(f in OBJECT_FLAGS for f in flags),
            "clean": not flags, "flags": flags}


def majority(votes: list[bool]) -> bool | None:
    """다수결 — 동점은 False (통과로 치지 않는다). 표가 없으면 None."""
    if not votes:
        return None
    return sum(votes) * 2 > len(votes)


def cohen_kappa(a: list[bool], b: list[bool]) -> float | None:
    """두 평가자의 예/아니오 일치도. 둘 다 한 값만 쓰면(기대 일치 1) None."""
    n = len(a)
    if n == 0 or n != len(b):
        return None
    po = sum(x == y for x, y in zip(a, b)) / n
    pa, pb = sum(a) / n, sum(b) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    if pe >= 1:
        return None
    return (po - pe) / (1 - pe)


def auc(scores: list[float], labels: list[bool]) -> float | None:
    """점수가 높을수록 통과(True)일 확률 — 순위 기반 AUC (동점은 0.5). 한쪽 라벨뿐이면 None."""
    pos = [s for s, y in zip(scores, labels) if y]
    neg = [s for s, y in zip(scores, labels) if not y]
    if not pos or not neg:
        return None
    wins = sum((p > q) + 0.5 * (p == q) for p in pos for q in neg)
    return wins / (len(pos) * len(neg))


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def metric_value(row: dict, name: str):
    """results 한 줄에서 자동 지표 값 (judge.fidelity 처럼 점으로 중첩). 없으면 None, bool 은 0/1."""
    v = row
    for part in name.split("."):
        v = v.get(part) if isinstance(v, dict) else None
    if isinstance(v, bool):
        return float(v)
    return float(v) if isinstance(v, (int, float)) else None


def pct(k: int, n: int) -> str:
    return f"{k}/{n} ({k / n:.0%})" if n else "—"


def fmt(v, d=2) -> str:
    return "—" if v is None else f"{v:.{d}f}"


# ── 읽기 ────────────────────────────────────────
def latest_run() -> str | None:
    runs = sorted(p.name for p in (HERE / "runs").glob("*") if (p / "results.jsonl").exists())
    return runs[-1] if runs else None


def load_reviews(run_id: str) -> dict[str, dict]:
    """{평가자: {(file, repeat): 판정}} — TEMPLATE.csv 는 뺀다."""
    out = {}
    for p in sorted((HERE / "reviews" / run_id).glob("*.csv")):
        if p.stem == "TEMPLATE":
            continue
        with open(p, encoding="utf-8") as f:
            rows = {}
            for r in csv.DictReader(f):
                v = rater_verdict(r)
                if v is not None:
                    rows[(r["file"], int(r["repeat"]))] = v
        if rows:
            out[p.stem] = rows
    return out


# ── 보고서 ──────────────────────────────────────
def build_report(run_id: str, meta: dict, results: list[dict], gt: dict, reviews: dict) -> str:
    L = [f"# Carret eval — {run_id}", "",
         f"실행: {'전체 파이프라인' if meta.get('full') else 'analyze 만'} · repeat {meta.get('repeat', 1)}"
         f" · 프리셋 {meta.get('preset', '-')} · 사진 {len({r['file'] for r in results})}장"
         f" · 실행 {len(results)}번 · 오류 {sum(1 for r in results if r.get('error'))}번", ""]
    ok = [r for r in results if not r.get("error")]
    first = [r for r in ok if r["repeat"] == 1]   # 분석 정확도는 사진당 한 번만 센다

    # 1. 분석 정확도
    L += ["## 1. 분석 정확도 (정답 대비)", "", "| 항목 | 맞음 | 틀린 사진 (정답 → 분석) |", "|---|---|---|"]
    for field in ("photo_type", "wear_level", "text_level"):
        rows = [r for r in first if gt.get(r["file"], {}).get(field)]
        wrong = [r for r in rows if r.get(field) != gt[r["file"]][field]]
        detail = ", ".join(f"{r['file']} ({gt[r['file']][field]} → {r.get(field)})" for r in wrong) or "—"
        L.append(f"| {field} | {pct(len(rows) - len(wrong), len(rows))} | {detail} |")
    rows = [r for r in first if gt.get(r["file"], {}).get("photo_type")]
    if rows:
        kinds = ("document", "inside_view", "product")
        L += ["", "photo_type 혼동표 (행 = 정답, 열 = 분석)", "",
              "| 정답 \\ 분석 | " + " | ".join(kinds) + " | 기타 |", "|---|" + "---|" * (len(kinds) + 1)]
        for g in kinds:
            c = Counter(r.get("photo_type") for r in rows if gt[r["file"]]["photo_type"] == g)
            other = sum(v for k, v in c.items() if k not in kinds)
            L.append(f"| {g} | " + " | ".join(str(c.get(k, 0)) for k in kinds) + f" | {other} |")
    L.append("")
    if not meta.get("full"):
        return "\n".join(L)

    # 2. 경로 분포
    L += ["## 2. 경로 분포 (정답 사진 종류별)", "", "| 정답 종류 | 생성 | 배경 교체 | 원본 그대로 | 그 밖 |", "|---|---|---|---|---|"]
    by_kind = defaultdict(Counter)
    for r in ok:
        by_kind[gt.get(r["file"], {}).get("photo_type", "?")][r.get("mode")] += 1
    for kind, c in sorted(by_kind.items()):
        other = sum(v for k, v in c.items() if k not in ("generate", "composite", "original"))
        L.append(f"| {kind} | {c.get('generate', 0)} | {c.get('composite', 0)} | {c.get('original', 0)} | {other} |")
    reasons = Counter(r.get("composite_reason") for r in ok if r.get("composite_reason"))
    if reasons:
        L += ["", "생성하지 않은 이유: " + ", ".join(f"{k} {v}" for k, v in reasons.most_common())]
    L.append("")

    # 3. 사람 판정
    if not reviews:
        L += ["## 3. 사람 판정", "", f"채점표가 아직 없다 — reviews/{run_id}/TEMPLATE.csv 를 <이름>.csv 로 복사해 채운다", ""]
        return "\n".join(L)
    raters = sorted(reviews)
    verdict = {}
    for r in ok:
        key = (r["file"], r["repeat"])
        vs = [reviews[p][key] for p in raters if key in reviews[p]]
        if vs:
            verdict[key] = {"preserved": majority([v["preserved"] for v in vs]),
                            "clean": majority([v["clean"] for v in vs]),
                            "flags": Counter(f for v in vs for f in v["flags"]), "n": len(vs)}
    L += ["## 3. 사람 판정", "", f"평가자 {len(raters)}명 ({', '.join(raters)}) · 채점된 실행 {len(verdict)}/{len(ok)}", "",
          "| 경로 | 보존 통과 (물건 변화 없음) | 결함 없음 (배경·구도 포함) |", "|---|---|---|"]
    for mode in ("generate", "composite", "original"):
        ks = [(r["file"], r["repeat"]) for r in ok if r.get("mode") == mode and (r["file"], r["repeat"]) in verdict]
        L.append(f"| {mode} | {pct(sum(verdict[k]['preserved'] for k in ks), len(ks))}"
                 f" | {pct(sum(verdict[k]['clean'] for k in ks), len(ks))} |")
    ks = [k for k in verdict]
    L.append(f"| **전체** | {pct(sum(verdict[k]['preserved'] for k in ks), len(ks))}"
             f" | {pct(sum(verdict[k]['clean'] for k in ks), len(ks))} |")

    L += ["", "정답 사진 종류별 보존 통과", "", "| 정답 종류 | 보존 통과 |", "|---|---|"]
    for kind in sorted({gt.get(f, {}).get("photo_type", "?") for f, _ in verdict}):
        kk = [k for k in verdict if gt.get(k[0], {}).get("photo_type", "?") == kind]
        L.append(f"| {kind} | {pct(sum(verdict[k]['preserved'] for k in kk), len(kk))} |")

    if meta.get("repeat", 1) > 1:
        per_item = defaultdict(list)
        for (f, _), v in verdict.items():
            per_item[f].append(v["preserved"])
        n = len(per_item)
        L += ["", f"물건 기준 (repeat {meta['repeat']}): 한 번이라도 통과 {pct(sum(any(v) for v in per_item.values()), n)}"
              f" · 매번 통과 {pct(sum(all(v) for v in per_item.values()), n)}"]

    flags = Counter()
    for v in verdict.values():
        flags.update({f: 1 for f in v["flags"]})
    if flags:
        L += ["", "실패 유형 (한 명이라도 표시한 실행 수): " + ", ".join(f"{k} {v}" for k, v in flags.most_common())]
    L.append("")

    # 4. 평가자 일치도
    if len(raters) >= 2:
        L += ["## 4. 평가자 일치도 (보존 통과, Cohen's kappa)", "", "| 평가자 쌍 | 같이 본 실행 | 일치 | kappa |", "|---|---|---|---|"]
        for i, p in enumerate(raters):
            for q in raters[i + 1:]:
                common = sorted(set(reviews[p]) & set(reviews[q]))
                a = [reviews[p][k]["preserved"] for k in common]
                b = [reviews[q][k]["preserved"] for k in common]
                agree = sum(x == y for x, y in zip(a, b))
                L.append(f"| {p} · {q} | {len(common)} | {pct(agree, len(common))} | {fmt(cohen_kappa(a, b))} |")
        L.append("")

    # 5. 자동 지표 vs 사람 (생성본만 — 배경 교체·원본은 물건 픽셀이 원본이라 볼 게 없다)
    gen = [r for r in ok if r.get("mode") == "generate" and (r["file"], r["repeat"]) in verdict]
    L += ["## 5. 자동 지표 vs 사람 (생성본, 보존 통과 기준)", "",
          f"생성본 {len(gen)}개. AUC 0.5 = 동전 던지기, 1.0 = 완벽히 가름. 상관은 점-이연 (Pearson)", "",
          "| 지표 | 값 있는 실행 | AUC | 상관 |", "|---|---|---|---|"]
    for m in AUTO_METRICS:
        pairs = [(metric_value(r, m), verdict[(r["file"], r["repeat"])]["preserved"]) for r in gen]
        pairs = [(s, y) for s, y in pairs if s is not None]
        s, y = [p[0] for p in pairs], [p[1] for p in pairs]
        L.append(f"| {m} | {len(pairs)} | {fmt(auc(s, y))} | {fmt(pearson(s, [float(v) for v in y]))} |")
    L.append("")
    return "\n".join(L)


def main() -> int:
    run_id = sys.argv[1] if len(sys.argv) > 1 else latest_run()
    if not run_id:
        print("runs/ 에 실행 결과가 없다 — python eval/run.py 먼저")
        return 1
    run_dir = HERE / "runs" / run_id
    meta = json.loads((run_dir / "meta.json").read_text(encoding="utf-8"))
    results = [json.loads(line) for line in (run_dir / "results.jsonl").read_text(encoding="utf-8").splitlines() if line]
    gt = {e["file"]: e for e in json.loads((HERE / "dataset.json").read_text(encoding="utf-8"))}
    text = build_report(run_id, meta, results, gt, load_reviews(run_id))
    (run_dir / "report.md").write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
