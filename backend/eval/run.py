"""평가 실행 — dataset.json 의 사진을 파이프라인에 돌려 결과를 results/runs/<run_id>/ 에 모은다.

    cd backend
    python eval/run.py --analyze-only            # analyze(VLM)만 — 분류 정확도, 사진당 약 $0.005
    python eval/run.py                           # 전체 파이프라인 — 생성 포함, 사진당 약 $0.04
    python eval/run.py --repeat 2 --only book.webp,bike.webp
    python eval/run.py --split test --repeat 2   # 동결된 test 만 (dev 는 조정용)
    python eval/run.py --set failure --repeat 2  # 실패 모음만 (dataset.json 의 set="failure")
    python eval/run.py --lock-file eval/data/locks/surface.txt   # 잠금 문구만 바꿔 보기 (코드는 그대로, meta 에 문구가 남는다)

라벨(photo_type · wear_level · text_level)이 안 된 사진은 건너뛴다 — 정답 없이 돌리면 집계가 틀린다.

전체 실행이 끝나면 사람 채점용 파일이 생긴다:
  results/runs/<run_id>/review.html        원본 | 결과를 나란히 (브라우저로 열기)
  results/reviews/<run_id>/TEMPLATE.csv    채점표 — 평가자마다 <이름>.csv 로 복사해 채운다 (README 참고)

앱의 storage·DB 는 건드리지 않는다 — 이 실행 전용 폴더(results/runs/<run_id>/storage, carret.db)를 쓴다.
"""
import argparse
import csv
import hashlib
import html
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGES = HERE / "data" / "images"
COST_ANALYZE, COST_FULL = 0.005, 0.045   # 사진 1장 대략 (09-26 실측 기준)
LABEL_VALUES = {"photo_type": ("document", "inside_view", "product"),   # intake.ENUMS 와 같게
                "wear_level": ("none", "light", "heavy"),
                "text_level": ("none", "simple", "dense")}
REVIEW_COLS = ["file", "repeat", "mode", "reviewed", "shape_color_changed", "text_changed",
               "wear_changed", "added_content", "background_issue", "framing_issue", "failure_tags",
               "photo_quality", "note"]


def _isolate(run_dir: Path) -> None:
    """app 을 import 하기 전에 — settings 가 import 시점에 env 를 읽는다."""
    os.environ["STORAGE_BACKEND"] = "local"
    os.environ["STORAGE_DIR"] = str(run_dir / "storage")
    os.environ["DB_PATH"] = str(run_dir / "carret.db")
    os.environ["KEEP_ATTEMPTS"] = "true"     # 게이트에 걸린 생성본도 files/ 에 남긴다


STOP_AFTER_DETECT_FAILED = 3   # 연속으로 이만큼 analyze 가 실패하면 멈춘다 (한도 초과·장애 — 09-29 에 48건을 버렸다)


def _preflight(detector, data: bytes) -> str | None:
    """돌리기 전에 VLM 을 한 번 불러 본다 — 한도 초과(429)·키 문제면 비용 쓰기 전에 멈춘다.
    문제가 있으면 이유를, 없으면 None."""
    try:
        detector.analyze(data)
    except Exception as e:
        return f"{type(e).__name__}: {str(e)[:200]}"
    return None


def _git() -> dict:
    """코드 버전 — dirty 면 커밋에 없는 변경이 섞인 실행이다 (그 diff 의 해시도 남긴다)."""
    def out(*cmd):
        try:
            return subprocess.run(["git", *cmd], cwd=HERE, capture_output=True, text=True, timeout=10).stdout
        except (OSError, subprocess.SubprocessError):
            return ""
    diff = out("diff", "HEAD", "--", str(HERE.parent / "app"))
    return {"commit": out("rev-parse", "--short", "HEAD").strip() or None,
            "app_dirty": bool(diff),
            "app_diff_sha": hashlib.sha1(diff.encode()).hexdigest()[:10] if diff else None}


def _conditions() -> dict:
    """결과를 바꾸는 조건 — 실행끼리 비교할 때 같은 조건인지 숫자만 보고 알 수 있게. app import 뒤에 부른다."""
    from app.core.config import settings
    from app.prompts.presets import SECONDHAND_LOCK
    from app.services.ai.generator import GEN_STEPS
    return {"gen_model": settings.fal_model, "gen_steps": GEN_STEPS, "vlm_model": settings.VLM_MODEL,
            "lock_sha": hashlib.sha1(SECONDHAND_LOCK.encode()).hexdigest()[:10], "lock": SECONDHAND_LOCK,
            "added_text_gate": settings.added_text_gate,
            **_git()}


def _override_lock(text: str) -> None:
    """잠금 문구를 이 실행에서만 바꾼다 — 원래 문구는 LEGACY 로 넘겨, Langfuse 프리셋에 들어 있어도 떼어내게."""
    from app.prompts import presets
    presets.LEGACY_LOCKS = (*presets.LEGACY_LOCKS, presets.SECONDHAND_LOCK)
    presets.SECONDHAND_LOCK = text


def _confirm(n: int, full: bool, yes: bool) -> bool:
    cost = n * (COST_FULL if full else COST_ANALYZE)
    print(f"사진 {n}번 실행 · 예상 비용 약 ${cost:.2f} ({'전체 파이프라인' if full else 'analyze 만'})")
    if yes:
        return True
    return input("진행할까요? [y/N] ").strip().lower() == "y"


def _analyze_row(detector, e: dict, data: bytes) -> dict:
    out = detector.analyze(data)
    return {k: out.get(k) for k in ("item", "photo_type", "wear_level", "watermark", "text_level")}


def composition_for(e: dict, mode: str) -> str | None:
    """--composition: "" = 안 씀, "auto" = dataset.json 의 target_composition, 그 밖 = 그 구도 키 하나."""
    if not mode:
        return None
    return e.get("target_composition") or None if mode == "auto" else mode


def gate_composition(key: str | None, view: str | None) -> tuple[str | None, str | None]:
    """구도는 그 각도로 찍힌 사진에만 붙인다 — 다른 각도면 생성이 안 보이던 면을 지어낸다 (study 10-01 §4,
    10-03 시험). 앱은 구도 고르는 화면(compositions.options)에서 막고, eval 은 여기서 막는다.
    반환: (붙일 구도, 안 붙인 이유)."""
    from app.services import compositions
    if not key:
        return None, None
    views = compositions.BY_KEY.get(key, {}).get("views", set())
    if view is None:
        return None, f"각도를 모름 — {key} 안 붙임"
    if view not in views:
        return None, f"각도 {view} 는 {key} 구도 각도({' · '.join(sorted(views))})가 아님 — 안 붙임"
    return key, None


def _view_of(data: bytes) -> str | None:
    """사진 한 장의 각도 (앱의 여러 장 업로드와 같은 classify_views, VLM 1회 · 낮은 해상도). 실패면 None."""
    from app.services.ai import detector
    try:
        return detector.classify_views([data])["photos"][0]["view"]
    except Exception as ex:
        print(f"  각도 분류 실패: {type(ex).__name__}: {str(ex)[:100]}")
        return None


def _composition_in(key: str, prompt: str | None) -> bool:
    from app.services import compositions
    text = compositions.BY_KEY.get(key, {}).get("prompt")
    return bool(text and prompt and text in prompt)


def _full_row(e: dict, data: bytes, rep: int, preset: str, files_dir: Path, composition: str | None = None) -> dict:
    from app.services import pipeline
    from app.services.persistence import storage, store
    fid = uuid.uuid4().hex
    ext = Path(e["file"]).suffix.lower()
    storage.save("original", f"{fid}{ext}", data)
    store.record_original(fid, ext, "eval", original_name=e["file"], size_bytes=len(data))
    t0 = time.time()
    # 팔 물건을 고르는 화면이 없다 — 정답 개수(dataset.json item_count)가 있으면 그대로 (10-03)
    out = pipeline.run_transform(fid, preset, composition=composition, answer_count=e.get("item_count"))
    elapsed = time.time() - t0
    name = f"{fid}_{preset}"
    inspect = json.loads(storage.load("quality", f"{name}_inspect.json") or b"{}")
    quality = json.loads(storage.load("quality", f"{name}.json") or b"{}")
    stem = f"{Path(e['file']).stem}__r{rep}"
    shutil.copyfile(IMAGES / e["file"], files_dir / f"{stem}_orig{ext}")
    (files_dir / f"{stem}_result.jpg").write_bytes(storage.load("result", f"{name}.jpg"))
    tries = []
    for n in range(1, 10):          # 생성 시도마다 (배경 교체로 끝나도 생성본이 남는다)
        img = storage.load("attempts", f"{name}_try{n}.jpg")
        if img is None:
            break
        (files_dir / f"{stem}_try{n}.jpg").write_bytes(img)
        tries.append(f"files/{stem}_try{n}.jpg")
    return {
        "tries": tries,
        "file_id": fid, "elapsed_s": round(elapsed, 1),
        "orig": f"files/{stem}_orig{ext}", "result": f"files/{stem}_result.jpg",
        **{k: inspect.get(k) for k in (
            "item", "photo_type", "wear_level", "watermark", "text_level", "mode",
            "composite_reason", "gate_passed", "verify_failed", "detect_failed",
            "visual_similarity", "item_similarity", "item_patch_similarity", "gen_attempts")},
        "gate_checks": inspect.get("gate_checks") or [],
        "added_text": inspect.get("added_text") or [],
        "gate_added_text": inspect.get("gate_added_text") or [],
        "checks": inspect.get("checks") or [],
        "judge": {k: quality.get(k) for k in ("fidelity", "realism", "trust")} if quality else None,
        "prompt_used": out.get("prompt_used"),
        # 실제로 붙었나 — 파이프라인이 더 빼는 경우가 있다 (물건 여러 개 · 생성 안 하는 경로)
        "composition": composition if composition and _composition_in(composition, out.get("prompt_used")) else None,
    }


def _write_review(run_id: str, run_dir: Path, rows: list, gt: dict) -> None:
    """채점표 템플릿 + 나란히 보기 HTML."""
    rdir = HERE / "results" / "reviews" / run_id
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
  <details><summary>쓴 프롬프트</summary><p class="prompt">{html.escape(r.get('prompt_used') or '-')}</p></details>
</section>""")
    (run_dir / "review.html").write_text(f"""<!doctype html><meta charset="utf-8">
<title>Carret eval {html.escape(run_id)}</title>
<style>body{{font:14px system-ui,sans-serif;margin:24px;background:#f6f6f4;color:#222}}
section{{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px 16px;margin:0 0 20px}}
h2{{font-size:16px;margin:0 0 4px}}.meta{{color:#555;margin:2px 0}}
.pair{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:8px}}.pair img{{width:100%}}
details{{margin-top:8px;color:#555}}.prompt{{white-space:pre-wrap;background:#f6f6f4;padding:8px;border-radius:6px}}</style>
<h1>Carret eval {html.escape(run_id)} — 원본 | 결과</h1>
<p>채점표: backend/eval/reviews/{html.escape(run_id)}/TEMPLATE.csv 를 &lt;이름&gt;.csv 로 복사해 채운다</p>
{''.join(cards)}""", encoding="utf-8")
    print(f"채점: {run_dir / 'review.html'} · {rdir / 'TEMPLATE.csv'}")


SETS = ("failure", "core")   # failure = 실패 모음 (dev 전용), core = 그 밖의 전부


def select(dataset: list[dict], only: str = "", split: str = "", set_: str = "") -> list[dict]:
    """돌릴 사진 고르기 — 조건은 모두 겹쳐 건다 (--set failure --only a.webp 면 실패 모음 안의 a 만).
    게시글의 두 번째 사진부터(post_index > 1)는 한 장 평가 대상이 아니라 빼고, --only 로 이름을 대면 넣는다."""
    if only:
        keep = {s.strip() for s in only.split(",")}
        dataset = [e for e in dataset if e["file"] in keep]
    else:
        dataset = [e for e in dataset if (e.get("post_index") or 1) == 1]
    if split:
        dataset = [e for e in dataset if (e.get("split") or "dev") == split]
    if set_:
        dataset = [e for e in dataset if (e.get("set") == "failure") == (set_ == "failure")]
    return dataset


# 정답으로 쓰는 칸만 — note · labeled_by · set(실패 모음) 같은 칸을 고쳐도 해시가 안 바뀌게
DATASET_LABELS = ("photo_type", "wear_level", "text_level", "key_texts", "item", "item_count")   # item_count: 여러 개면 경로가 바뀐다


def dataset_sha(entries: list[dict], images: Path) -> str:
    """돌린 사진들의 정답 라벨 + 사진 내용 해시 — 실행끼리 같은 데이터로 돌렸는지 (compare.py 가 대 본다)."""
    h = hashlib.sha1()
    for e in sorted(entries, key=lambda e: e["file"]):
        label = {k: e.get(k) for k in ("file",) + DATASET_LABELS}
        h.update(json.dumps(label, ensure_ascii=False, sort_keys=True).encode())
        p = images / e["file"]
        h.update(hashlib.sha1(p.read_bytes()).digest() if p.exists() else b"missing")
    return h.hexdigest()[:10]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--analyze-only", action="store_true")
    ap.add_argument("--repeat", type=int, default=1, help="같은 사진을 몇 번 돌리나 (생성은 매번 다르다)")
    ap.add_argument("--preset", default="studio_white")
    ap.add_argument("--only", default="", help="쉼표로 구분한 파일 이름")
    ap.add_argument("--split", choices=("dev", "test"), default="",
                    help="이 split 만 (split 이 없는 옛 항목은 dev)")
    ap.add_argument("--set", choices=SETS, default="", dest="set_",
                    help="failure = 실패 모음만, core = 실패 모음을 뺀 나머지")
    ap.add_argument("--run-id", default="")
    ap.add_argument("--note", default="", help="이 실행에서 바꾼 것 (meta.json 에 남는다 — 코드에 없는 임시 변경은 꼭 적는다)")
    ap.add_argument("--yes", action="store_true", help="비용 확인을 건너뛴다")
    ap.add_argument("--composition", default="",
                    help="정석 구도 프롬프트를 붙인다 — auto = 사진마다 dataset.json 의 target_composition, 또는 구도 키 하나. "
                         "사진 각도(classify_views)가 그 구도 각도일 때만 붙는다")
    ap.add_argument("--lock-file", default="", help="잠금 문구를 이 파일 내용으로 바꿔 돌린다 (프롬프트 실험용)")
    a = ap.parse_args()
    lock = ""
    if a.lock_file:
        path = Path(a.lock_file)
        for base in (HERE, HERE / "data"):   # eval/ · eval/data/ 기준 경로도 받는다 (locks/x.txt)
            if not path.exists() and (base / path).exists():
                path = base / path
        if not path.is_file():
            print(f"잠금 파일이 없다: {a.lock_file}")
            return 1
        lock = " ".join(path.read_text(encoding="utf-8").split())
        if not lock:
            print(f"{a.lock_file} 이 비었다")
            return 1

    dataset = select(json.loads((HERE / "data" / "dataset.json").read_text(encoding="utf-8")), a.only, a.split, a.set_)
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
    run_dir = HERE / "results" / "runs" / run_id
    files_dir = run_dir / "files"
    files_dir.mkdir(parents=True, exist_ok=True)
    _isolate(run_dir)
    sys.path.insert(0, str(HERE.parent))
    from app.core import db
    from app.core.logsetup import setup_logging
    setup_logging()   # 파이프라인 진행 로그(logger.info)가 콘솔에 보이게
    from app.services.ai import detector
    db.init_db()
    if lock:
        _override_lock(lock)

    problem = _preflight(detector, (IMAGES / dataset[0]["file"]).read_bytes())
    if problem:
        print(f"VLM 호출이 안 된다 — 멈춤: {problem}")
        return 1

    meta = {"run_id": run_id, "full": full, "repeat": reps, "preset": a.preset,
            "split": a.split or "all", "set": a.set_ or "all", "only": a.only, "composition": a.composition or None, "note": a.note, "lock_file": Path(a.lock_file).name if a.lock_file else None,
            "dataset_sha": dataset_sha(dataset, IMAGES),
            "created": datetime.now().isoformat(timespec="seconds"), **_conditions()}
    (run_dir / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    # 한 줄씩 바로 쓴다 — 중간에 멈춰도(Ctrl-C·한도) 끝난 실행은 남는다
    results = open(run_dir / "results.jsonl", "w", encoding="utf-8")
    rows = []
    streak = 0
    for e in dataset:
        data = (IMAGES / e["file"]).read_bytes()
        comp, skipped, view = None, None, None
        if full and a.composition:
            want = composition_for(e, a.composition)
            view = _view_of(data) if want else None
            comp, skipped = gate_composition(want, view)
            if skipped:
                print(f"  [{e['file']}] {skipped}")
        for rep in range(1, reps + 1):
            row = {"file": e["file"], "repeat": rep}
            if full and a.composition:
                row.update(view=view, composition_wanted=composition_for(e, a.composition),
                           composition_skipped=skipped)
            try:
                row.update(_full_row(e, data, rep, a.preset, files_dir, comp) if full
                           else _analyze_row(detector, e, data))
            except Exception as ex:
                row["error"] = f"{type(ex).__name__}: {ex}"
            print(f"[{e['file']} r{rep}] {row.get('mode', 'analyze')} "
                  f"{row.get('photo_type')} {row.get('error', '')}", flush=True)
            rows.append(row)
            results.write(json.dumps(row, ensure_ascii=False) + "\n")
            results.flush()
            streak = streak + 1 if (row.get("detect_failed") or row.get("error")) else 0
            if streak >= STOP_AFTER_DETECT_FAILED:
                break
        if streak >= STOP_AFTER_DETECT_FAILED:
            print(f"analyze 실패가 {streak}번 연속 — 한도 초과·장애로 보고 멈춘다 (지금까지 결과는 저장)")
            break

    results.close()
    if full:
        _write_review(run_id, run_dir, rows, {e["file"]: e for e in dataset})
    print(f"끝: {run_dir} — python eval/report.py {run_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
