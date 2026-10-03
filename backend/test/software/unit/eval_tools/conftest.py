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


@pytest.fixture
def compare_mod(tmp_path, monkeypatch):
    """compare 와 그 안의 report(rp) 의 HERE 를 tmp_path 로 — 실제 runs/·reviews/ 를 건드리지 않게.
    compare 는 불러올 때 sys.path 에 eval/ 를 넣고 `import report` 를 한다 — 테스트 뒤 sys.path·sys.modules 를 되돌린다."""
    import sys
    monkeypatch.setattr(sys, "path", list(sys.path))
    had_report = "report" in sys.modules
    mod = _load("compare")
    monkeypatch.setattr(mod, "HERE", tmp_path)
    monkeypatch.setattr(mod.rp, "HERE", tmp_path)
    yield mod
    if not had_report:
        sys.modules.pop("report", None)


@pytest.fixture
def board_mod(tmp_path, monkeypatch):
    """board 와 그 안의 report(rp) 의 HERE, intake.DATASET 를 tmp_path 로 — 실제 runs/·reviews/·dataset.json 을 건드리지 않게.
    board 는 불러올 때 sys.path 에 eval/ 를 넣고 `import intake`, `import report` 를 한다 — 테스트 뒤 되돌린다."""
    import sys
    monkeypatch.setattr(sys, "path", list(sys.path))
    had = {m: m in sys.modules for m in ("intake", "report")}
    mod = _load("board")
    monkeypatch.setattr(mod, "HERE", tmp_path)
    monkeypatch.setattr(mod.rp, "HERE", tmp_path)
    monkeypatch.setattr(mod.intake, "DATASET", tmp_path / "dataset.json")
    (tmp_path / "runs").mkdir()
    yield mod
    for m, was in had.items():
        if not was:
            sys.modules.pop(m, None)
