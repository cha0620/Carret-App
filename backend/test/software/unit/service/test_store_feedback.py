"""app.services.store 의 save_feedback/get_feedback 엣지 케이스.

store.py 자체는 수정 대상이 아니고(이미 구현됨), db.init_db() 가 만드는
SQLite 스키마(UNIQUE(file_id, preset_key), CHECK(rating BETWEEN 1 AND 5))와
upsert 동작을 검증한다. 실제 DB 파일을 건드리지 않도록 매 테스트마다
settings.db_path 를 tmp_path 로 바꿔치기한다.
"""
import sqlite3

import pytest

from app.core import db
from app.core.config import settings
from app.services import store

FID = "0123456789abcdef0123456789abcdef"


@pytest.fixture()
def feedback_db(monkeypatch, tmp_path):
    db_path = tmp_path / "test_feedback.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    db.init_db()
    return db_path


def test_save_then_get_feedback_roundtrip(feedback_db):
    store.save_feedback(FID, "studio_white", 4, "good")
    row = store.get_feedback(FID, "studio_white")
    assert row["file_id"] == FID
    assert row["preset_key"] == "studio_white"
    assert row["rating"] == 4
    assert row["comment"] == "good"
    assert row["created_at"] is not None
    assert row["updated_at"] is not None


def test_get_feedback_missing_returns_none(feedback_db):
    assert store.get_feedback("nope", "nope") is None


def test_save_feedback_upserts_same_key(feedback_db):
    """같은 (file_id, preset_key) 로 재전송하면 새 row 가 아니라 갱신."""
    store.save_feedback(FID, "studio_white", 2, "meh")
    first = store.get_feedback(FID, "studio_white")

    store.save_feedback(FID, "studio_white", 5, "great")
    second = store.get_feedback(FID, "studio_white")

    assert second["rating"] == 5
    assert second["comment"] == "great"
    assert second["id"] == first["id"]          # 같은 row (upsert), 신규 삽입 아님


def test_save_feedback_distinct_preset_keys_independent(feedback_db):
    store.save_feedback(FID, "studio_white", 3, None)
    store.save_feedback(FID, "studio_black", 5, None)

    a = store.get_feedback(FID, "studio_white")
    b = store.get_feedback(FID, "studio_black")
    assert a["rating"] == 3
    assert b["rating"] == 5


def test_save_feedback_comment_optional(feedback_db):
    store.save_feedback(FID, "no_comment", 3, None)
    row = store.get_feedback(FID, "no_comment")
    assert row["comment"] is None


def test_save_feedback_rating_below_range_raises_integrity_error(feedback_db):
    """DB CHECK(rating BETWEEN 1 AND 5) — Pydantic 검증을 우회해 store 를
    직접 호출하는 경우(e.g. 내부 배치 스크립트)에도 잘못된 rating 은 막혀야 한다."""
    with pytest.raises(sqlite3.IntegrityError):
        store.save_feedback(FID, "bad_low", 0)


def test_save_feedback_rating_above_range_raises_integrity_error(feedback_db):
    with pytest.raises(sqlite3.IntegrityError):
        store.save_feedback(FID, "bad_high", 6)
