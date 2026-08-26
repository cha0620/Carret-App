import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    from main import app
    return TestClient(app)


@pytest.fixture()
def tmp_storage(monkeypatch, tmp_path):
    """저장소를 임시 디렉터리로 격리 (실파일 무상처)."""
    monkeypatch.setattr("app.services.storage.BASE", tmp_path)
    for sub in ("original", "result", "quality",
                "dataset/img", "dataset/after"):
        (tmp_path / sub).mkdir(parents=True, exist_ok=True)
    return tmp_path