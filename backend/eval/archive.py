"""eval 결과 보관 — 숫자는 eval.db 하나에, 사진은 S3 에 (10-19).

  python eval/archive.py build      results/runs · data 를 읽어 eval/eval.db 를 새로 만든다
  python eval/archive.py push       eval.db 의 files 목록 중 S3 에 없는 것 + eval.db 를 올린다
  python eval/archive.py pull       S3 에서 eval.db 를 받고, 목록대로 빠진 파일을 받아 제자리에 푼다 (--force: 로컬 것도 덮음)

backend 에서 실행. S3 는 앱과 같은 버킷·키(.env), 접두어만 따로 <s3_prefix>-eval/.
pull 은 해시가 같은 파일은 건너뛰고, 로컬이 다르면 덮지 않는다 — 새 작업 환경에서 .env 만 있으면 된다.
돈이 드는 모델 호출 없음 (S3 저장·전송 비용만).
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
RUNS = HERE / "results" / "runs"
DB = HERE / "eval.db"
# 보관 대상 — eval 기준 자료 전체 + 남긴 run 폴더 (10-19 정리 뒤)
ROOTS = [HERE / "data", RUNS]
_SKIP_SUFFIX = {".part", ".pull", ".tmp", ".lock"}   # pull · build 중간 파일은 보관하지 않는다

SCHEMA = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY,
    model TEXT,                -- 생성 모델 (posts 행의 model, 없으면 NULL)
    n_samples INTEGER
);
CREATE TABLE samples (
    id INTEGER PRIMARY KEY,
    run_id TEXT NOT NULL REFERENCES runs(run_id),
    post TEXT, object TEXT, label TEXT,
    main TEXT,                 -- 대표 입력 사진 이름 (eval/data/images 또는 posts)
    file_id TEXT,              -- 이 run 안의 저장 id (storage/ · honesty_labels 키)
    mode TEXT, gate_passed INTEGER, composite_reason TEXT, gen_attempts INTEGER, elapsed_s REAL,
    item TEXT, photo_type TEXT, verify_failed INTEGER, detect_failed INTEGER,
    visual_sim REAL, item_sim REAL, item_patch_sim REAL,
    prompt_used TEXT,
    result_path TEXT,          -- eval/ 기준 경로 = S3 키 끝부분
    extra TEXT                 -- posts 행 나머지 (JSON). 같은 post·object 가 다시 돈 행도 있어 UNIQUE 없음
);
CREATE TABLE attempts (
    sample_id INTEGER NOT NULL REFERENCES samples(id),
    try_no INTEGER NOT NULL,
    path TEXT NOT NULL,
    PRIMARY KEY (sample_id, try_no)
);
CREATE TABLE labels (
    sample_id INTEGER REFERENCES samples(id),
    key TEXT PRIMARY KEY,      -- honesty_labels.json 의 키 그대로
    same INTEGER, added INTEGER, missing INTEGER, changed INTEGER, unsure INTEGER,
    memo TEXT, labeled_at INTEGER
);
CREATE TABLE files (
    path TEXT PRIMARY KEY,     -- eval/ 기준 상대 경로
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL
);
"""

_INSPECT = ("item", "photo_type", "verify_failed", "detect_failed",
            "visual_similarity", "item_similarity", "item_patch_similarity")
_COLS = {"post", "object", "label", "main", "mode", "gate_passed", "composite_reason",
         "gen_attempts", "elapsed_s", "prompt_used", "result", "tries"}


def _rel(p: Path) -> str:
    return p.relative_to(HERE).as_posix()


def _name_to_fid(run_dir: Path) -> dict[str, str]:
    db = run_dir / "carret.db"
    if not db.exists():
        return {}
    with sqlite3.connect(db) as c:
        return {n: f for f, n in c.execute("SELECT file_id, original_name FROM originals")}


def _sha(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _inside(p: str) -> Path:
    """eval.db 에 적힌 경로가 eval/ 밖(../ · 절대경로)을 가리키면 거부 — 받은 목록으로 .env·코드를 덮지 않게."""
    dst = (HERE / p).resolve()
    if not dst.is_relative_to(HERE):
        raise ValueError(f"eval/ 밖 경로: {p}")
    return dst


def build() -> None:
    tmp = DB.with_suffix(".db.tmp")   # 다 만든 뒤 바꿔치기 — 중간에 죽어도 예전 eval.db 가 남게
    tmp.unlink(missing_ok=True)
    with sqlite3.connect(tmp) as c:
        _fill(c)
        n = {t: c.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in ("runs", "samples", "attempts", "labels", "files")}
        size = c.execute("SELECT sum(size) FROM files").fetchone()[0] or 0
        unlinked = c.execute("SELECT count(*) FROM labels WHERE sample_id IS NULL").fetchone()[0]
    c.close()
    tmp.replace(DB)
    print(f"eval.db: {n} · 파일 {size / 1e6:.1f}MB · 연결 안 된 라벨 {unlinked}")


def _fill(c: sqlite3.Connection) -> None:
    c.executescript(SCHEMA)
    for posts in sorted(RUNS.glob("*/posts.json")):
        rd = posts.parent
        rows = json.loads(posts.read_text(encoding="utf-8"))
        fids = _name_to_fid(rd)
        c.execute("INSERT INTO runs VALUES (?,?,?)",
                  (rd.name, next((r.get("model") for r in rows if r.get("model")), None), len(rows)))
        for r in rows:
            fid = fids.get(r.get("main"))
            q = rd / "storage" / "quality" / f"{fid}_studio_white_inspect.json"
            ins = json.loads(q.read_text(encoding="utf-8")) if fid and q.exists() else {}
            cur = c.execute(
                "INSERT INTO samples (run_id, post, object, label, main, file_id, mode, gate_passed,"
                " composite_reason, gen_attempts, elapsed_s, item, photo_type, verify_failed, detect_failed,"
                " visual_sim, item_sim, item_patch_sim, prompt_used, result_path, extra)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (rd.name, r.get("post"), r.get("object"), r.get("label"), r.get("main"), fid,
                 r.get("mode"), r.get("gate_passed"), r.get("composite_reason"), r.get("gen_attempts"),
                 r.get("elapsed_s"), *(ins.get(k) for k in _INSPECT), r.get("prompt_used"),
                 _rel(rd / "files" / r["result"]) if r.get("result") else None,
                 json.dumps({k: v for k, v in r.items() if k not in _COLS}, ensure_ascii=False)))
            for i, t in enumerate(r.get("tries") or [], 1):
                c.execute("INSERT INTO attempts VALUES (?,?,?)", (cur.lastrowid, i, _rel(rd / "files" / t)))
    labels = HERE / "data" / "honesty_labels.json"
    for key, v in (json.loads(labels.read_text(encoding="utf-8")) if labels.exists() else {}).items():
        run, _, stem = key.partition("/")
        sid = c.execute("SELECT id FROM samples WHERE run_id=? AND file_id=?",
                        (run, stem.rsplit("_studio_white", 1)[0])).fetchone()
        c.execute("INSERT INTO labels VALUES (?,?,?,?,?,?,?,?,?)",
                  (sid[0] if sid else None, key, *(v.get(k) for k in ("same", "added", "missing", "changed", "unsure")),
                   v.get("memo"), v.get("at")))
    for root in ROOTS:
        for p in sorted(root.rglob("*")):
            if p.is_file() and "__pycache__" not in p.parts and p.suffix not in _SKIP_SUFFIX and p.name != ".DS_Store":
                c.execute("INSERT INTO files VALUES (?,?,?)", (_rel(p), p.stat().st_size, _sha(p)))


def _s3():
    sys.path.insert(0, str(HERE.parent))
    from app.core.config import settings
    from app.services.persistence.storage import S3Backend
    b = S3Backend()
    if not b.bucket:
        sys.exit("S3_BUCKET 이 비어 있다 (.env)")
    return b.s3, b.bucket, f"{settings.s3_prefix}-eval"


def _remote(s3, bucket: str, prefix: str) -> set[str]:
    return {o["Key"] for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix + "/")
            for o in page.get("Contents", [])}


def _remote_sha(s3, bucket: str, key: str) -> str | None:
    return s3.head_object(Bucket=bucket, Key=key).get("Metadata", {}).get("sha256")


def push() -> None:
    """S3 쪽 sha256 메타데이터와 다를 때만 올린다 — 크기가 같고 내용만 바뀐 라벨 json 도 올라가게."""
    s3, bucket, prefix = _s3()
    have = _remote(s3, bucket, prefix)
    with sqlite3.connect(DB) as c:
        files = c.execute("SELECT path, sha256 FROM files").fetchall()
    missing = [p for p, _ in files if not (HERE / p).is_file()]
    if missing:   # build 뒤에 지워진 파일 — 반쯤 올리고 멈추지 않게 먼저 막는다
        sys.exit(f"build 뒤에 없어진 파일 {len(missing)}개 (예: {missing[0]}) — build 를 다시 하라")
    changed = [p for p, sha in files if _sha(HERE / p) != sha]
    if changed:   # build 뒤에 내용이 바뀐 파일 — 예전 해시를 붙여 올리면 pull 이 영영 못 받는다
        sys.exit(f"build 뒤에 내용이 바뀐 파일 {len(changed)}개 (예: {changed[0]}) — build 를 다시 하라")
    up = 0
    for p, sha in files:
        key = f"{prefix}/{p}"
        if key in have and _remote_sha(s3, bucket, key) == sha:
            continue
        s3.upload_file(str(HERE / p), bucket, key, ExtraArgs={"Metadata": {"sha256": sha}})
        up += 1
        if up % 100 == 0:
            print(f"  {up}개 올림", flush=True)
    s3.upload_file(str(DB), bucket, f"{prefix}/eval.db")
    print(f"올림 {up}개 (같은 것 {len(files) - up}개) + eval.db → s3://{bucket}/{prefix}/")


def pull(force: bool = False) -> None:
    """S3 목록대로 받는다. 로컬에 내용이 다른 파일이 있으면 덮지 않고 알린다 (--force 면 덮음)."""
    s3, bucket, prefix = _s3()
    if DB.exists() and not force:
        sys.exit("로컬 eval.db 가 이미 있다 — push 안 한 build 를 덮을 수 있어 멈춘다 (--force 로 덮음)")
    tmp_db = DB.with_suffix(".db.pull")
    s3.download_file(bucket, f"{prefix}/eval.db", str(tmp_db))
    with sqlite3.connect(tmp_db) as c:
        files = c.execute("SELECT path, sha256 FROM files").fetchall()
    c.close()
    got = same = kept = bad = 0
    for p, sha in files:
        dst = _inside(p)
        if dst.exists():
            if _sha(dst) == sha:
                same += 1
                continue
            if not force:
                kept += 1
                print(f"  로컬이 달라 그대로 둠: {p}")
                continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        part = dst.with_name(dst.name + ".part")
        s3.download_file(bucket, f"{prefix}/{p}", str(part))
        if _sha(part) != sha:
            part.unlink()
            bad += 1
            print(f"  해시 다름(안 씀): {p}")
            continue
        part.replace(dst)
        got += 1
    tmp_db.replace(DB)
    print(f"받음 {got}개 · 같은 것 {same}개 · 로컬이 달라 둔 것 {kept}개 · 해시 다름 {bad}개")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["build", "push", "pull"])
    ap.add_argument("--force", action="store_true", help="pull: 로컬 eval.db·내용 다른 파일도 덮는다")
    a = ap.parse_args()
    pull(a.force) if a.cmd == "pull" else {"build": build, "push": push}[a.cmd]()
