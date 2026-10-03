"""backend/eval/*.py 를 파일 경로로 불러온다 — 디렉터리 이름 `eval` 이 builtin 과 겹쳐 import 가 헷갈린다."""
import importlib.util
from pathlib import Path

import pytest

EVAL_DIR = Path(__file__).resolve().parents[4] / "eval"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(f"carret_eval_{name}", EVAL_DIR / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def report_mod():
    return _load("report")


@pytest.fixture
def run_mod():
    return _load("run")


@pytest.fixture
def fetch_mod():
    return _load("fetch")


@pytest.fixture
def intake_mod(tmp_path, monkeypatch):
    """모듈 경로(DATASET·IMAGES·INBOX·LABELS_CSV)를 tmp_path 로 돌려 둔 intake."""
    mod = _load("intake")
    images = tmp_path / "images"
    inbox = images / "inbox"
    inbox.mkdir(parents=True)
    monkeypatch.setattr(mod, "IMAGES", images)
    monkeypatch.setattr(mod, "INBOX", inbox)
    monkeypatch.setattr(mod, "DATASET", tmp_path / "dataset.json")
    monkeypatch.setattr(mod, "LABELS_CSV", inbox / "labels.csv")
    monkeypatch.setattr(mod, "SPLIT_LOG", tmp_path / "splits.jsonl")
    return mod


@pytest.fixture
def grade_mod(tmp_path, monkeypatch):
    """HERE 를 tmp_path 로 돌려 둔 grade — 실제 runs/·reviews/ 를 건드리지 않게."""
    mod = _load("grade")
    monkeypatch.setattr(mod, "HERE", tmp_path)
    return mod


@pytest.fixture
def refs_mod():
    return _load("refs")
