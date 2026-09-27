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
