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
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(file_id, preset_key)
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


def init_db():
    p = Path(settings.db_path)
    if str(p) != ":memory:":
        p.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as c:
        c.executescript(SCHEMA)
        _migrate(c)