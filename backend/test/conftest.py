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

    # ⭐ BACKEND 싱글톤도 강제로 로컬로 — 안 그러면 .env에 STORAGE_BACKEND=s3가
    # 박혀있는 로컬 환경에서 테스트가 실제 AWS로 나간다 (BASE만 바꿔서는 무의미:
    # S3Backend는 BASE를 안 쓴다).
    from app.services import storage as storage_module
    monkeypatch.setattr(storage_module, "BACKEND", storage_module.LocalBackend())

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