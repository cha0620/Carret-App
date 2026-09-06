import io

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from pathlib import Path
from dotenv import load_dotenv

# 프로젝트 루트의 .env 읽기
env_path = Path(__file__).parent.parent / ".env"
if env_path.exists():
    load_dotenv(env_path)

@pytest.fixture(autouse=True)
def isolated_storage(monkeypatch, tmp_path):
    """⭐ 두 문(BASE / storage_dir) 을 하나의 tmp 로."""
    root = tmp_path / "storage"
    for sub in ("original", "result", "quality",
                "dataset/img", "dataset/after"):
        (root / sub).mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr("app.services.storage.BASE", root)

    from app.core.config import settings
    monkeypatch.setattr(settings, "storage_dir", str(root), raising=False)
    return root


@pytest.fixture()
def tmp_storage():
    """테스트에서 현재 루트 꺼내 쓰는 용도."""
    from app.services import storage
    return storage.BASE


@pytest.fixture()
def client():
    from main import app
    return TestClient(app)


@pytest.fixture()
def make_png():
    def _make(w=64, h=64, color=(120, 90, 60)):
        buf = io.BytesIO()
        Image.new("RGB", (w, h), color).save(buf, format="PNG")
        return buf.getvalue()
    return _make