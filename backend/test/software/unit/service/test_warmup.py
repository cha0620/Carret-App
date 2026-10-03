"""app.core.warmup — 서버 시작 뒤 로컬 모델 미리 올리기."""
import pytest

from app.core import warmup
from app.core.config import settings
from app.services.ai import compositor, embedder


@pytest.fixture()
def loads(monkeypatch):
    seen = []
    monkeypatch.setattr(embedder, "_load", lambda: seen.append("dino"))
    monkeypatch.setattr(compositor, "_load", lambda: seen.append("rembg"))
    return seen


@pytest.mark.parametrize("backend,expected", [("fal", ["dino"]), ("local", ["dino", "rembg"])])
def test_warm_models_loads_rembg_only_for_local_cutout(monkeypatch, loads, backend, expected):
    monkeypatch.setattr(settings, "cutout_backend", backend)
    assert warmup.warm_models() == expected and loads == expected


def test_warm_models_swallows_load_failure(monkeypatch):
    def boom():
        raise OSError("no disk")
    monkeypatch.setattr(embedder, "_load", boom)
    monkeypatch.setattr(settings, "cutout_backend", "fal")
    assert warmup.warm_models() == []


@pytest.mark.parametrize("enabled,mode,starts", [(True, "real", True), (False, "real", False),
                                                  (True, "mock", False)])
def test_start_warmup_runs_in_background_only_when_enabled_and_real(monkeypatch, loads,
                                                                     enabled, mode, starts):
    monkeypatch.setattr(settings, "warmup_models", enabled)
    monkeypatch.setattr(settings, "pipeline_mode", mode, raising=False)
    monkeypatch.setattr(settings, "cutout_backend", "fal")
    th = warmup.start_warmup()
    assert (th is not None) is starts
    if th:
        th.join(5)
        assert th.daemon and loads == ["dino"]
