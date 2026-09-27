"""storage/quality 의 inspect JSON + 성적표를 모아 수치 요약 (무료, API 호출 없음).

사용: python .claude/skills/eval-report/inspect_stats.py [--dir backend/storage/quality] [--preset studio_white]
"""
import argparse
import json
import statistics
from collections import Counter
from pathlib import Path


def _dist(xs):
    xs = sorted(x for x in xs if isinstance(x, (int, float)))
    if not xs:
        return "n=0"
    q = statistics.quantiles(xs, n=4) if len(xs) >= 4 else [xs[0], statistics.median(xs), xs[-1]]
    return (f"n={len(xs)} min={xs[0]:.3f} p25={q[0]:.3f} med={statistics.median(xs):.3f} "
            f"p75={q[-1]:.3f} max={xs[-1]:.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="backend/storage/quality")
    ap.add_argument("--preset")
    a = ap.parse_args()
    root = Path(a.dir)
    rows = []
    for p in sorted(root.glob("*_inspect.json")):
        stem = p.name[: -len("_inspect.json")]
        file_id, _, preset = stem.partition("_")
        if a.preset and preset != a.preset:
            continue
        ins = json.loads(p.read_text(encoding="utf-8"))
        judge_p = root / f"{stem}.json"
        judge = json.loads(judge_p.read_text(encoding="utf-8")) if judge_p.exists() else None
        rows.append((file_id, preset, ins, judge))

    n = len(rows)
    print(f"# inspect 요약 ({n}건, {root})\n")
    if not n:
        return
    ins = [r[2] for r in rows]
    pct = lambda k: f"{k}/{n} ({k / n:.0%})"

    print("## 경로")
    for mode, c in Counter(i.get("mode", "generate") for i in ins).most_common():
        print(f"- mode={mode}: {pct(c)}")
    for reason, c in Counter(i.get("composite_reason") for i in ins if i.get("composite_reason")).most_common():
        print(f"  - composite_reason={reason}: {c}")
    print(f"- status=blocked: {pct(sum(i.get('status') == 'blocked' for i in ins))}")
    print(f"- detect_failed: {pct(sum(bool(i.get('detect_failed')) for i in ins))}")
    print(f"- verify_failed: {pct(sum(bool(i.get('verify_failed')) for i in ins))}")
    print(f"- gate_passed=True: {pct(sum(i.get('gate_passed') is True for i in ins))}")
    print(f"- gate_retried: {pct(sum(bool(i.get('gate_retried')) for i in ins))}")
    print(f"- guard_failed (seed 재시도 후에도): {pct(sum(bool(i.get('guard_failed')) for i in ins))}")

    print("\n## 가드 (guard_report 에 남은 실패)")
    fails = Counter(g["name"] for i in ins for g in i.get("guard_report", []) if not g.get("passed", False))
    for name, c in fails.most_common() or [("(없음)", 0)]:
        print(f"- {name}: {c}")

    print("\n## 분포")
    print(f"- visual_similarity (전체 DINO): {_dist(i.get('visual_similarity') for i in ins)}")
    print(f"- item_similarity (누끼 DINO): {_dist(i.get('item_similarity') for i in ins)}")
    print(f"- item_patch_similarity (누끼 DINO 패치 하위 1%): {_dist(i.get('item_patch_similarity') for i in ins)}")
    print(f"- ocr_local_recall (EasyOCR): {_dist(i.get('ocr_local_recall') for i in ins)}")
    print(f"- gen_attempts: {Counter(i.get('gen_attempts') for i in ins)}")
    print(f"- text_level (detect): {Counter(i.get('text_level') for i in ins)}")

    judged = [r[3] for r in rows if r[3]]
    print(f"\n## judge ({len(judged)}/{n} 채점됨)")
    for axis in ("fidelity", "realism", "trust"):
        print(f"- {axis}: {_dist(j.get(axis) for j in judged)}")

    for key in ("item_similarity", "item_patch_similarity"):
        low = [(r[0][:8], r[1], r[2].get(key)) for r in rows
               if isinstance(r[2].get(key), (int, float))]
        if low:
            print(f"\n## {key} 낮은 순 5건 (눈으로 확인할 후보)")
            for fid, preset, v in sorted(low, key=lambda x: x[2])[:5]:
                print(f"- {fid} {preset}: {v:.3f}")


if __name__ == "__main__":
    main()
