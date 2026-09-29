"""평가 실행 — dataset.json 의 사진을 파이프라인에 돌려 결과를 runs/<run_id>/ 에 모은다.

    cd backend
    python eval/run.py --analyze-only            # analyze(VLM)만 — 분류 정확도, 사진당 약 $0.005
    python eval/run.py                           # 전체 파이프라인 — 생성 포함, 사진당 약 $0.04
    python eval/run.py --repeat 2 --only book.webp,bike.webp
    python eval/run.py --split test --repeat 2   # 동결된 test 만 (dev 는 조정용)

라벨(photo_type · wear_level · text_level)이 안 된 사진은 건너뛴다 — 정답 없이 돌리면 집계가 틀린다.

전체 실행이 끝나면 사람 채점용 파일이 생긴다:
  runs/<run_id>/review.html        원본 | 결과를 나란히 (브라우저로 열기)
  reviews/<run_id>/TEMPLATE.csv    채점표 — 평가자마다 <이름>.csv 로 복사해 채운다 (README 참고)

앱의 storage·DB 는 건드리지 않는다 — 이 실행 전용 폴더(runs/<run_id>/storage, carret.db)를 쓴다.
"""
import argparse
import csv
import html
import json
import os
import shutil
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGES = HERE / "images"
COST_ANALYZE, COST_FULL = 0.005, 0.04   # 사진 1장 대략 (09-26 실측 기준)
LABEL_VALUES = {"photo_type": ("document", "inside_view", "product"),   # intake.ENUMS 와 같게
                "wear_level": ("none", "light", "heavy"),
                "text_level": ("none", "simple", "dense")}
REVIEW_COLS = ["file", "repeat", "mode", "reviewed", "shape_color_changed", "text_changed",
               "wear_changed", "background_issue", "framing_issue", "note"]


def _isolate(run_dir: Path) -> None:
    """app 을 import 하기 전에 — settings 가 import 시점에 env 를 읽는다."""
    os.environ["STORAGE_BACKEND"] = "local"
    os.environ["STORAGE_DIR"] = str(run_dir / "storage")
    os.environ["DB_PATH"] = str(run_dir / "carret.db")


def _confirm(n: int, full: bool, yes: bool) -> bool:
    cost = n * (COST_FULL if full else COST_ANALYZE)
    print(f"사진 {n}번 실행 · 예상 비용 약 ${cost:.2f} ({'전체 파이프라인' if full else 'analyze 만'})")
    if yes:
        return True
    return input("진행할까요? [y/N] ").strip().lower() == "y"


def _analyze_row(detector, e: dict, data: bytes) -> dict:
    out = detector.analyze(data)
    return {k: out.get(k) for k in ("item", "photo_type", "wear_level", "watermark", "text_level")}


def _full_row(e: dict, data: bytes, rep: int, preset: str, files_dir: Path) -> dict:
    from app.services import pipeline
    from app.services.persistence import storage, store
    fid = uuid.uuid4().hex
    ext = Path(e["file"]).suffix.lower()
    storage.save("original", f"{fid}{ext}", data)
    store.record_original(fid, ext, "eval", original_name=e["file"], size_bytes=len(data))
    t0 = time.time()
    out = pipeline.run_transform(fid, preset)
    elapsed = time.time() - t0
    name = f"{fid}_{preset}"
    inspect = json.loads(storage.load("quality", f"{name}_inspect.json") or b"{}")
    quality = json.loads(storage.load("quality", f"{name}.json") or b"{}")
    stem = f"{Path(e['file']).stem}__r{rep}"
    shutil.copyfile(IMAGES / e["file"], files_dir / f"{stem}_orig{ext}")
    (files_dir / f"{stem}_result.jpg").write_bytes(storage.load("result", f"{name}.jpg"))
    return {
        "file_id": fid, "elapsed_s": round(elapsed, 1),
        "orig": f"files/{stem}_orig{ext}", "result": f"files/{stem}_result.jpg",
        **{k: inspect.get(k) for k in (
            "item", "photo_type", "wear_level", "watermark", "text_level", "mode",
            "composite_reason", "gate_passed", "verify_failed", "detect_failed",
            "visual_similarity", "item_similarity", "item_patch_similarity", "gen_attempts")},
        "gate_checks": inspect.get("gate_checks") or [],
        "checks": inspect.get("checks") or [],
        "judge": {k: quality.get(k) for k in ("fidelity", "realism", "trust")} if quality else None,
        "prompt_used": out.get("prompt_used"),
    }


def _write_review(run_id: str, run_dir: Path, rows: list, gt: dict) -> None:
    """채점표 템플릿 + 나란히 보기 HTML."""
    rdir = HERE / "reviews" / run_id
    rdir.mkdir(parents=True, exist_ok=True)
    with open(rdir / "TEMPLATE.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLS)
        w.writeheader()
        for r in rows:
            if r.get("error"):
                continue
            w.writerow({"file": r["file"], "repeat": r["repeat"], "mode": r.get("mode")})

    cards = []
    for r in rows:
        if r.get("error"):
            continue
        g = gt.get(r["file"], {})
        texts = ", ".join(g.get("key_texts") or []) or "—"
        cards.append(f"""
<section>
  <h2>{html.escape(r['file'])} · repeat {r['repeat']}</h2>
  <p class="meta">경로 <b>{html.escape(str(r.get('mode')))}</b> ({html.escape(str(r.get('composite_reason') or '-'))})
   · 분석 {html.escape(str(r.get('photo_type')))} / 하자 {html.escape(str(r.get('wear_level')))}
   · 정답 {html.escape(str(g.get('photo_type')))} / 하자 {html.escape(str(g.get('wear_level')))}
   · 지켜야 할 글자: {html.escape(texts)}</p>
  <p class="meta">{html.escape(g.get('note') or '')}</p>
  <div class="pair"><img src="{html.escape(r['orig'])}" alt="원본"><img src="{html.escape(r['result'])}" alt="결과"></div>
</section>""")
    (run_dir / "review.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>Carret eval {html.escape(run_id)}</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;background:#f6f6f4;color:#222}}
section{{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:0 0 20px}}
h2{{font-size:16px;margin:0 0 4px}}.meta{{color:#555;margin:2px 0}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}}.pair img{{width:100%}}</style>
<h1>Carret eval {html.escape(run_id)} — 원본 | 결과</h1>
<p>채점표: backend/eval/reviews/{html.escape(run_id)}/TEMPLATE.csv 를 &lt;이름&gt;.csv 로 복사해 채운다</p>
{''.join(cards)}""", encoding="utf-8")
    print(f"채점: {run_dir / 'review.html'} · {rdir / 'TEMPLATE.csv'}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analyze-only", action="store_true")
    ap.add_argument("--repeat", type=int, default=1, help="같은 사진을 몇 번 돌리나 (생성은 매번 다르다)")
    ap.add_argument("--preset", default="studio_white")
    ap.add_argument("--only", default="", help="쉼표로 구분한 파일 이름")
    ap.add_argument("--split", choices=("dev", "test"), default="",
                    help="이 split 만 (split 이 없는 옛 항목은 dev)")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--yes", action="store_true", help="비용 확인을 건너뛴다")
    a = ap.parse_args()

    dataset = json.loads((HERE / "dataset.json").read_text(encoding="utf-8"))
    if a.only:
        keep = {s.strip() for s in a.only.split(",")}
        dataset = [e for e in dataset if e["file"] in keep]
    if a.split:
        dataset = [e for e in dataset if (e.get("split") or "dev") == a.split]
    unlabeled = [e["file"] for e in dataset
                 if not all(e.get(k) in v for k, v in LABEL_VALUES.items())]
    if unlabeled:
        print(f"라벨이 안 된 사진 {len(unlabeled)}장은 건너뛴다: {', '.join(unlabeled)}")
        dataset = [e for e in dataset if e["file"] not in unlabeled]
    if not dataset:
        print("돌릴 사진이 없다")
        return 1
    missing = [e["file"] for e in dataset if not (IMAGES / e["file"]).exists()]
    if missing:
        print(f"images/ 에 없는 사진: {missing} — python eval/fetch.py 먼저")
        return 1
    full = not a.analyze_only
    reps = a.repeat if full else 1
    if not _confirm(len(dataset) * reps, full, a.yes):
        return 1

    run_id = a.run_id or datetime.now().strftime("%Y%m%d-%H%M") + ("-full" if full else "-analyze")
    run_dir = HERE / "runs" / run_id
    files_dir = run_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    _isolate(run_dir)
    sys.path.insert(0, str(HERE.parent))
    from app.core import db
    from app.services.ai import detector
    db.init_db()

    rows = []
    for e in dataset:
        data = (IMAGES / e["file"]).read_bytes()
        for rep in range(1, reps + 1):
            row = {"file": e["file"], "repeat": rep}
            try:
                row.update(_full_row(e, data, rep, a.preset, files_dir) if full
                           else _analyze_row(detector, e, data))
            except Exception as ex:
                row["error"] = f"{type(ex).__name__}: {ex}"
            print(f"[{e['file']} r{rep}] {row.get('mode', 'analyze')} "
                  f"{row.get('photo_type')} {row.get('error', '')}")
            rows.append(row)

    meta = {"run_id": run_id, "full": full, "repeat": reps, "preset": a.preset,
            "split": a.split or "all", "only": a.only,
            "created": datetime.now().isoformat(timespec="seconds")}
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    with open(run_dir / "results.jsonl", "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    if full:
        _write_review(run_id, run_dir, rows, {e["file"]: e for e in dataset})
    print(f"끝: {run_dir} — python eval/report.py {run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
