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

    monkeypatch.setattr("app.services.persistence.storage.BASE", root)

    from app.core.config import settings
    monkeypatch.setattr(settings, "storage_dir", str(root), raising=False)

    # ⭐ BACKEND 싱글톤도 강제로 로컬로 — 안 그러면 .env에 STORAGE_BACKEND=s3가
    # 박혀있는 로컬 환경에서 테스트가 실제 AWS로 나간다 (BASE만 바꿔서는 무의미:
    # S3Backend는 BASE를 안 쓴다).
    from app.services.persistence import storage as storage_module
    monkeypatch.setattr(storage_module, "BACKEND", storage_module.LocalBackend())

    return root


@pytest.fixture(autouse=True)
def no_text_lock_vlm(monkeypatch):
    """파이프라인의 read_text 단계는 실제 VLM 을 부른다 — 유닛 테스트에선 기본으로
    끄고, 그 기능을 검증하는 테스트만 켠다 (네트워크·비용 0 유지)."""
    from app.core.config import settings
    monkeypatch.setattr(settings, "text_lock", False)


@pytest.fixture(autouse=True)
def no_real_cutout_model(monkeypatch):
    """배경 교체 모드의 rembg 모델(수백 MB)을 유닛 테스트가 실수로 띄우지 않게 —
    부르면 바로 실패. 합성을 검증하는 테스트는 compose/cutout_alpha 를 직접 바꿔 끼운다."""
    from app.services.ai import compositor

    def _no_model():
        raise RuntimeError("유닛 테스트에서 실제 오리기 모델 금지")
    monkeypatch.setattr(compositor, "_load", _no_model)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    """⭐ 실제 프로덕션 SQLite 파일(data/carret.db)을 절대 건드리지 않도록
    매 테스트마다 격리된 tmp db 로 바꿔치기.

    store.record_original/record_result 가 파이프라인(pipeline.run_transform)과
    업로드/인박스 라우트에 실제로 연결된 이후로는, 이 fixture 없이 `client` 를
    쓰는 통합 테스트나 pipeline_mod.run_transform() 을 직접 부르는 테스트가
    (개별적으로 settings.db_path 를 바꿔치기하지 않는 한) 그대로 진짜 DB 파일에
    row 를 남긴다 — db.get_conn() 이 호출 시점마다 settings.db_path 를 새로
    읽으므로 여기서 한 번만 바꿔치기해도 이후 모든 app 코드 경로에 적용됨.

    일부 테스트(test_store_feedback.py, test_db_migrate.py 등)는 자체적으로
    settings.db_path 를 다시 몽키패치하고 db.init_db() 를 재호출하는데,
    이 fixture 뒤에 덮어쓰는 것이라 문제없이 공존한다."""
    from app.core import db
    from app.core.config import settings

    db_path = tmp_path / "test_isolated.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    db.init_db()
    return db_path


@pytest.fixture()
def tmp_storage():
    """테스트에서 현재 루트 꺼내 쓰는 용도."""
    from app.services.persistence import storage
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