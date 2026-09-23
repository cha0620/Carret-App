"""POST /dev/auto-feedback — VLM(모의)이 대신 남긴 피드백을 feedbacks 테이블에
source="agent"로 저장하는지, 원본/결과 파일이 없을 때 404 인지 검증.
"""
import pytest

import app.services.ai.auto_feedback as auto_feedback_mod
from app.core import db
from app.core.config import settings
from app.services.persistence import storage

FID = "0123456789abcdef0123456789abcdef"


@pytest.fixture()
def feedback_db(monkeypatch, tmp_path):
    """실제 backend/data/*.db 를 건드리지 않도록 요청마다 격리된 sqlite 파일 사용."""
    db_path = tmp_path / "dev_auto_feedback.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    db.init_db()
    return db_path


def test_dev_auto_feedback_returns_200_with_agent_source(client, feedback_db, make_png, monkeypatch):
    storage.save("original", f"{FID}.jpg", make_png())
    storage.save("result", f"{FID}_studio_white.jpg", make_png())

    def fake_generate_feedback(original, result):
        return {"rating": 4, "comment": "괜찮은 결과네요"}

    monkeypatch.setattr(auto_feedback_mod, "generate_feedback", fake_generate_feedback)
    r = client.post("/dev/auto-feedback",
                    json={"file_id": FID, "preset": "studio_white"})

    assert r.status_code == 200
    body = r.json()
    assert body["file_id"] == FID
    assert body["preset_key"] == "studio_white"
    assert body["rating"] == 4
    assert body["comment"] == "괜찮은 결과네요"
    assert body["source"] == "agent"


def test_dev_auto_feedback_does_not_overwrite_existing_user_feedback(client, feedback_db, make_png, monkeypatch):
    from app.services.persistence import store
    storage.save("original", f"{FID}.jpg", make_png())
    storage.save("result", f"{FID}_studio_white.jpg", make_png())
    store.save_feedback(FID, "studio_white", 5, "real user comment", source="user")

    monkeypatch.setattr(auto_feedback_mod, "generate_feedback",
                        lambda original, result: {"rating": 1, "comment": "synthetic"})
    r = client.post("/dev/auto-feedback",
                    json={"file_id": FID, "preset": "studio_white"})

    assert r.status_code == 200
    body = r.json()
    assert body["rating"] == 5
    assert body["comment"] == "real user comment"
    assert body["source"] == "user"


def test_dev_auto_feedback_404_when_original_missing(client, feedback_db, make_png):
    storage.save("result", f"{FID}_studio_white.jpg", make_png())

    r = client.post("/dev/auto-feedback",
                    json={"file_id": FID, "preset": "studio_white"})
    assert r.status_code == 404


def test_dev_auto_feedback_404_when_result_missing(client, feedback_db, make_png):
    storage.save("original", f"{FID}.jpg", make_png())

    r = client.post("/dev/auto-feedback",
                    json={"file_id": FID, "preset": "studio_white"})
    assert r.status_code == 404
