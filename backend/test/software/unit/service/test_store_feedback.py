"""app.services.persistence.store 의 save_feedback/get_feedback 엣지 케이스.

store.py 자체는 수정 대상이 아니고(이미 구현됨), db.init_db() 가 만드는
SQLite 스키마(UNIQUE(file_id, preset_key), CHECK(rating BETWEEN 1 AND 5))와
upsert 동작을 검증한다. 실제 DB 파일을 건드리지 않도록 매 테스트마다
settings.db_path 를 tmp_path 로 바꿔치기한다.
"""
import sqlite3

import pytest

from app.core import db
from app.core.config import settings
from app.services.persistence import store

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


# ── source="agent" (auto feedback) — 실사용자 신호 보호 ────────────────
def test_save_feedback_agent_source_creates_row_when_none_exists(feedback_db):
    store.save_feedback(FID, "agent_new", 4, "괜찮네요", source="agent")
    row = store.get_feedback(FID, "agent_new")
    assert row["rating"] == 4
    assert row["comment"] == "괜찮네요"
    assert row["source"] == "agent"


def test_save_feedback_agent_source_does_not_overwrite_existing_user_row(feedback_db):
    store.save_feedback(FID, "protected", 5, "real user comment", source="user")
    before = store.get_feedback(FID, "protected")

    store.save_feedback(FID, "protected", 1, "synthetic comment", source="agent")
    after = store.get_feedback(FID, "protected")

    assert after["rating"] == before["rating"] == 5
    assert after["comment"] == before["comment"] == "real user comment"
    assert after["source"] == "user"
    assert after["id"] == before["id"]


def test_save_feedback_agent_source_overwrites_existing_agent_row(feedback_db):
    store.save_feedback(FID, "agent_upsert", 2, "first", source="agent")
    first = store.get_feedback(FID, "agent_upsert")

    store.save_feedback(FID, "agent_upsert", 5, "second", source="agent")
    second = store.get_feedback(FID, "agent_upsert")

    assert second["rating"] == 5
    assert second["comment"] == "second"
    assert second["source"] == "agent"
    assert second["id"] == first["id"]


def test_save_feedback_user_source_always_overwrites_regardless_of_prior_source(feedback_db):
    """기존 동작 회귀 방지: source="user"(기본값)는 이전 row의 source가
    무엇이었든(agent 든 user 든) 항상 덮어써야 한다."""
    store.save_feedback(FID, "user_wins", 1, "agent first", source="agent")

    store.save_feedback(FID, "user_wins", 5, "real user now")
    row = store.get_feedback(FID, "user_wins")

    assert row["rating"] == 5
    assert row["comment"] == "real user now"
    assert row["source"] == "user"


def test_user_and_agent_feedback_are_kept_side_by_side(feedback_db):
    """사람이 코멘트를 달아도 에이전트 코멘트가 사라지지 않는다 (source 별로 한 줄)."""
    store.save_feedback(FID, "studio_white", 5, "에이전트 의견", source="agent")
    store.save_feedback(FID, "studio_white", 2, "로고가 깨졌어요", source="user",
                        tags=["text_broken", "not_a_tag"])
    fb = store.get_feedbacks(FID, "studio_white")
    assert fb["agent"]["comment"] == "에이전트 의견" and fb["agent"]["rating"] == 5
    assert fb["user"]["comment"] == "로고가 깨졌어요"
    assert fb["user"]["tags"] == ["text_broken"]          # 모르는 태그는 버림
    assert store.get_feedback(FID, "studio_white")["source"] == "user"   # 대표 = 사람 것


def test_agent_rerun_updates_only_agent_row(feedback_db):
    store.save_feedback(FID, "p", 2, "사람", source="user")
    store.save_feedback(FID, "p", 4, "에이전트1", source="agent")
    store.save_feedback(FID, "p", 3, "에이전트2", source="agent")
    fb = store.get_feedbacks(FID, "p")
    assert fb["user"]["comment"] == "사람" and fb["agent"]["comment"] == "에이전트2"
