"""POST /api/feedback, GET /api/feedback/{file_id}/{preset_key} 엣지 케이스."""
import pytest

from app.core import db
from app.core.config import settings

FID = "0123456789abcdef0123456789abcdef"


@pytest.fixture()
def feedback_db(monkeypatch, tmp_path):
    """실제 backend/data/*.db 를 건드리지 않도록 요청마다 격리된 sqlite 파일 사용.

    get_conn() 은 호출 시점마다 settings.db_path 를 다시 읽으므로, main.py
    임포트(및 그때의 db.init_db() 호출) 이후라도 이 몽키패치가 바로 반영된다.
    """
    db_path = tmp_path / "api_feedback.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    db.init_db()
    return db_path


def test_submit_feedback_returns_200(client, feedback_db):
    r = client.post("/api/feedback", json={
        "file_id": FID, "preset_key": "studio_white",
        "rating": 4, "comment": "nice",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["file_id"] == FID
    assert body["preset_key"] == "studio_white"
    assert body["rating"] == 4
    assert body["comment"] == "nice"


def test_submit_feedback_upserts_same_file_and_preset(client, feedback_db):
    payload = {"file_id": FID, "preset_key": "studio_black", "rating": 2, "comment": "meh"}
    r1 = client.post("/api/feedback", json=payload)
    assert r1.status_code == 200

    payload2 = {**payload, "rating": 5, "comment": "great"}
    r2 = client.post("/api/feedback", json=payload2)
    assert r2.status_code == 200
    assert r2.json()["rating"] == 5

    got = client.get(f"/api/feedback/{FID}/studio_black")
    assert got.status_code == 200
    assert got.json()["rating"] == 5
    assert got.json()["comment"] == "great"


def test_get_feedback_missing_key_returns_404(client, feedback_db):
    r = client.get(f"/api/feedback/{FID}/does_not_exist")
    assert r.status_code == 404


def test_submit_feedback_comment_is_optional(client, feedback_db):
    r = client.post("/api/feedback", json={
        "file_id": FID, "preset_key": "no_comment", "rating": 3,
    })
    assert r.status_code == 200
    assert r.json()["comment"] is None


@pytest.mark.parametrize("rating", [0, 6, "five"])
def test_submit_feedback_invalid_rating_is_422(client, feedback_db, rating):
    r = client.post("/api/feedback", json={
        "file_id": FID, "preset_key": "p", "rating": rating,
    })
    assert r.status_code == 422


@pytest.mark.parametrize("bad_file_id", [
    "not-a-hex-id",
    "0123456789abcdef0123456789abcde",   # 31자 (한 글자 부족)
    "0123456789ABCDEF0123456789ABCDEF",  # 대문자는 패턴 불일치
])
def test_submit_feedback_invalid_file_id_is_422(client, feedback_db, bad_file_id):
    r = client.post("/api/feedback", json={
        "file_id": bad_file_id, "preset_key": "p", "rating": 3,
    })
    assert r.status_code == 422


def test_submit_feedback_missing_body_is_422(client, feedback_db):
    assert client.post("/api/feedback", json={}).status_code == 422


def test_submit_feedback_save_failure_returns_500(client, feedback_db, monkeypatch):
    from app.services import store

    def boom(*a, **kw):
        raise RuntimeError("db exploded")

    monkeypatch.setattr(store, "save_feedback", boom)
    r = client.post("/api/feedback", json={
        "file_id": FID, "preset_key": "p", "rating": 3,
    })
    assert r.status_code == 500
