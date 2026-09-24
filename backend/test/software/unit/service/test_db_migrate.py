"""app.core.db 의 `source` 컬럼 마이그레이션.

`CREATE TABLE IF NOT EXISTS`는 이미 존재하는(구버전) `feedbacks` 테이블의
컬럼을 바꾸지 않으므로, `_migrate()`가 `PRAGMA table_info`로 확인 후
`ALTER TABLE ... ADD COLUMN`을 하는지 검증한다. 실제 DB 파일을 건드리지
않도록 매 테스트마다 settings.db_path 를 tmp_path 로 바꿔치기한다.
"""
import sqlite3

from app.core import db
from app.core.config import settings

FID = "0123456789abcdef0123456789abcdef"


def test_fresh_db_feedbacks_table_has_source_column_default_user(monkeypatch, tmp_path):
    db_path = tmp_path / "fresh.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))

    db.init_db()

    with db.get_conn() as c:
        cols = {row["name"] for row in c.execute("PRAGMA table_info(feedbacks)")}
        assert "source" in cols

        # 구버전 INSERT 문(SOURCE 미지정)처럼 컬럼을 명시하지 않아도 기본값 적용.
        c.execute(
            "INSERT INTO feedbacks(file_id, preset_key, rating, comment) "
            "VALUES (?,?,?,?)",
            (FID, "legacy_insert", 3, "no source given"),
        )
        row = c.execute(
            "SELECT * FROM feedbacks WHERE file_id=? AND preset_key=?",
            (FID, "legacy_insert"),
        ).fetchone()
        assert row["source"] == "user"


def test_migrate_adds_source_column_to_preexisting_table_without_it(monkeypatch, tmp_path):
    """`_migrate()` 자체를 컬럼이 없는(마이그레이션 이전) 테이블에 직접 적용."""
    db_path = tmp_path / "legacy.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))

    # source 컬럼 없이 구버전 스키마로 직접 테이블을 만든다.
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE feedbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            preset_key TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file_id, preset_key)
        )
    """)
    cols_before = {row["name"] for row in conn.execute("PRAGMA table_info(feedbacks)")}
    assert "source" not in cols_before
    conn.close()

    # init_db()가 이 기존 파일 위에서 마이그레이션을 수행해야 한다.
    db.init_db()

    with db.get_conn() as c:
        cols_after = {row["name"] for row in c.execute("PRAGMA table_info(feedbacks)")}
        assert "source" in cols_after

        c.execute(
            "INSERT INTO feedbacks(file_id, preset_key, rating, comment) "
            "VALUES (?,?,?,?)",
            (FID, "legacy_row", 4, "still works"),
        )
        row = c.execute(
            "SELECT * FROM feedbacks WHERE file_id=? AND preset_key=?",
            (FID, "legacy_row"),
        ).fetchone()
        assert row["source"] == "user"


def test_migrate_is_idempotent_when_column_already_exists(monkeypatch, tmp_path):
    """이미 source 컬럼이 있는 DB에 init_db()를 다시 돌려도 에러 없이 통과."""
    db_path = tmp_path / "idempotent.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))

    db.init_db()
    db.init_db()  # 두 번째 호출도 ALTER TABLE 중복 에러 없이 성공해야 함

    with db.get_conn() as c:
        cols = {row["name"] for row in c.execute("PRAGMA table_info(feedbacks)")}
        assert "source" in cols


def test_migrate_rebuilds_old_unique_key_keeping_rows(monkeypatch, tmp_path):
    """옛 UNIQUE(file_id, preset_key) 테이블 → source 포함 키로 재생성, 행은 보존."""
    db_path = tmp_path / "old_unique.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE feedbacks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL, preset_key TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment TEXT, source TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file_id, preset_key))""")
    conn.execute("INSERT INTO feedbacks(file_id, preset_key, rating, comment, source) "
                 "VALUES (?,?,?,?,?)", (FID, "p", 4, "old agent", "agent"))
    conn.commit(); conn.close()

    db.init_db()
    db.init_db()   # 두 번 돌려도 안전

    from app.services.persistence import store
    store.save_feedback(FID, "p", 2, "new user", source="user")
    fb = store.get_feedbacks(FID, "p")
    assert fb["agent"]["comment"] == "old agent" and fb["agent"]["tags"] == []
    assert fb["user"]["comment"] == "new user"
