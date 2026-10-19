"""eval/archive.py build — 가짜 run 폴더로 eval.db 채우기 (S3 호출 없음)."""
import json
import sqlite3

import pytest

import importlib.util
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parents[4] / "eval"


def _load(name):
    spec = importlib.util.spec_from_file_location(f"carret_eval_{name}", EVAL_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def archive_mod(tmp_path, monkeypatch):
    mod = _load("archive")
    runs = tmp_path / "results" / "runs"
    monkeypatch.setattr(mod, "HERE", tmp_path)
    monkeypatch.setattr(mod, "RUNS", runs)
    monkeypatch.setattr(mod, "DB", tmp_path / "eval.db")
    monkeypatch.setattr(mod, "ROOTS", [tmp_path / "data", runs])
    return mod


def test_build_fills_samples_attempts_labels_files(archive_mod, tmp_path):
    rd = tmp_path / "results" / "runs" / "r1"
    (rd / "files").mkdir(parents=True)
    (rd / "storage" / "quality").mkdir(parents=True)
    (rd / "files" / "res.jpg").write_bytes(b"R")
    (rd / "files" / "t1.jpg").write_bytes(b"T1")
    (rd / "posts.json").write_text(json.dumps([
        {"post": "p1", "object": "bag", "main": "bag.webp", "model": "seedream", "mode": "generate",
         "result": "res.jpg", "tries": ["t1.jpg"], "foo": 1}]), encoding="utf-8")
    with sqlite3.connect(rd / "carret.db") as c:
        c.execute("CREATE TABLE originals (file_id TEXT, original_name TEXT)")
        c.execute("INSERT INTO originals VALUES ('fid1', 'bag.webp')")
    (rd / "storage" / "quality" / "fid1_studio_white_inspect.json").write_text(
        json.dumps({"item": "가방", "visual_similarity": 0.9}), encoding="utf-8")
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "honesty_labels.json").write_text(json.dumps({
        "r1/fid1_studio_white": {"same": 1, "memo": "ok", "at": 5},
        "zz/none_studio_white": {"same": 0}}), encoding="utf-8")

    archive_mod.build()

    c = sqlite3.connect(tmp_path / "eval.db")
    assert c.execute("SELECT * FROM runs").fetchall() == [("r1", "seedream", 1)]
    sid, fid, item, vs, rp, extra = c.execute(
        "SELECT id, file_id, item, visual_sim, result_path, extra FROM samples").fetchone()
    assert (fid, item, vs, rp) == ("fid1", "가방", 0.9, "results/runs/r1/files/res.jpg")
    assert json.loads(extra) == {"model": "seedream", "foo": 1}
    assert c.execute("SELECT * FROM attempts").fetchall() == [(sid, 1, "results/runs/r1/files/t1.jpg")]
    labels = dict(c.execute("SELECT key, sample_id FROM labels").fetchall())
    assert labels == {"r1/fid1_studio_white": sid, "zz/none_studio_white": None}
    paths = {p for (p,) in c.execute("SELECT path FROM files")}
    assert {"results/runs/r1/files/res.jpg", "results/runs/r1/posts.json",
            "data/honesty_labels.json"} <= paths


class _FakeS3:
    """download_file 만 흉내 — key → 바이트."""
    def __init__(self, objs):
        self.objs = objs

    def download_file(self, bucket, key, dest):
        Path(dest).write_bytes(self.objs[key])


def _remote_db(tmp_path, files):
    src = tmp_path / "remote.db"
    with sqlite3.connect(src) as c:
        c.execute("CREATE TABLE files (path TEXT, size INTEGER, sha256 TEXT)")
        c.executemany("INSERT INTO files VALUES (?,?,?)", files)
    c.close()
    return src.read_bytes()


def _sha(b):
    import hashlib
    return hashlib.sha256(b).hexdigest()


def _setup_pull(archive_mod, tmp_path, monkeypatch, files, objs):
    s3 = _FakeS3({"p/eval.db": _remote_db(tmp_path, files), **objs})
    monkeypatch.setattr(archive_mod, "_s3", lambda: (s3, "b", "p"))


def test_pull_rejects_path_outside_eval(archive_mod, tmp_path, monkeypatch):
    """보안: eval.db 목록에 ../ 경로가 있으면 eval/ 밖에 쓰지 않고 멈춘다."""
    _setup_pull(archive_mod, tmp_path, monkeypatch,
                [("../evil.txt", 1, _sha(b"X"))], {"p/../evil.txt": b"X"})
    with pytest.raises(ValueError):
        archive_mod.pull()
    assert not (tmp_path.parent / "evil.txt").exists()


def test_pull_keeps_different_local_file_unless_force(archive_mod, tmp_path, monkeypatch):
    """로컬 내용이 다르면 그대로 두고, --force 면 덮는다. 없는 파일은 받는다."""
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "a.json").write_bytes(b"LOCAL")
    files = [("data/a.json", 6, _sha(b"REMOTE")), ("data/b.json", 1, _sha(b"B"))]
    objs = {"p/data/a.json": b"REMOTE", "p/data/b.json": b"B"}
    _setup_pull(archive_mod, tmp_path, monkeypatch, files, objs)

    archive_mod.pull()
    assert (tmp_path / "data" / "a.json").read_bytes() == b"LOCAL"
    assert (tmp_path / "data" / "b.json").read_bytes() == b"B"
    assert (tmp_path / "eval.db").exists()

    archive_mod.pull(force=True)
    assert (tmp_path / "data" / "a.json").read_bytes() == b"REMOTE"


def test_pull_drops_download_with_wrong_hash(archive_mod, tmp_path, monkeypatch):
    """받은 내용의 해시가 목록과 다르면 쓰지 않는다 (.part 도 남기지 않음)."""
    _setup_pull(archive_mod, tmp_path, monkeypatch,
                [("data/c.json", 1, _sha(b"GOOD"))], {"p/data/c.json": b"BAD"})
    archive_mod.pull()
    assert list((tmp_path / "data").iterdir()) == []
