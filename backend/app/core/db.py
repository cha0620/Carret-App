"""SQLite 계층 — 메타데이터만. 바이트 금지."""
import sqlite3
from pathlib import Path

from app.core.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS originals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL UNIQUE,
    ext TEXT NOT NULL,
    source TEXT NOT NULL,
    original_name TEXT,
    size_bytes INTEGER,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL,
    preset_key TEXT NOT NULL,
    result_name TEXT NOT NULL,
    item TEXT,
    considered TEXT,
    gate_passed INTEGER,
    bubbles TEXT,
    elapsed_s REAL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(file_id, preset_key)
);
CREATE TABLE IF NOT EXISTS feedbacks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    file_id TEXT NOT NULL,
    preset_key TEXT NOT NULL,
    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
    comment TEXT,
    source TEXT NOT NULL DEFAULT 'user',
    tags TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(file_id, preset_key, source)
);
"""


def get_conn():
    # timeout=10: run-inbox가 여러 파일을 스레드풀로 돌리면서 각자
    # results/originals에 쓰다 보니 "database is locked"이 전보다 잦아질 수 있어
    # 기본 5s보다 여유를 둠. WAL: 쓰기 중에도 다른 커넥션이 읽을 수 있게.
    conn = sqlite3.connect(settings.db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _migrate(c):
    """기존에 만들어진 DB 파일에 새 컬럼을 뒤늦게 추가 (CREATE TABLE IF NOT EXISTS는
    이미 있는 테이블의 컬럼을 바꾸지 않으므로)."""
    cols = {row["name"] for row in c.execute("PRAGMA table_info(feedbacks)")}
    if "source" not in cols:
        c.execute("ALTER TABLE feedbacks ADD COLUMN source TEXT NOT NULL DEFAULT 'user'")
    if "tags" not in cols:
        c.execute("ALTER TABLE feedbacks ADD COLUMN tags TEXT")
    _migrate_feedback_unique(c)


def _migrate_feedback_unique(c):
    """UNIQUE(file_id, preset_key) → UNIQUE(file_id, preset_key, source).

    예전엔 한 결과에 피드백이 한 줄뿐이라 사람이 코멘트를 달면 에이전트 코멘트가
    사라졌다. 둘을 나란히 남기려면 source 까지 키에 넣어야 하는데, SQLite 는
    UNIQUE 제약을 바꾸는 ALTER 가 없어서 테이블을 새로 만들어 옮긴다 (행은 그대로)."""
    sql = c.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='feedbacks'").fetchone()
    if sql is None or "UNIQUE(file_id, preset_key, source)" in sql["sql"]:
        return
    c.executescript("""
        CREATE TABLE feedbacks_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            file_id TEXT NOT NULL,
            preset_key TEXT NOT NULL,
            rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
            comment TEXT,
            source TEXT NOT NULL DEFAULT 'user',
            tags TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(file_id, preset_key, source)
        );
        INSERT INTO feedbacks_new(id, file_id, preset_key, rating, comment, source, tags,
                                  created_at, updated_at)
            SELECT id, file_id, preset_key, rating, comment, source, tags, created_at, updated_at
            FROM feedbacks;
        DROP TABLE feedbacks;
        ALTER TABLE feedbacks_new RENAME TO feedbacks;
    """)


def init_db():
    p = Path(settings.db_path)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as c:
        c.executescript(SCHEMA)
        _migrate(c)