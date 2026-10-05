"""results 테이블의 경로 컬럼 (mode·composite_reason·photo_type·wear_level, 09-27).

- db: 새 DB 는 CREATE TABLE 에, 옛 스키마 DB 는 _migrate 가 ALTER ADD COLUMN 으로 붙인다
- store.record_result(route=...): 네 키만 넣고 없는 키는 NULL, 재변환 upsert 는 옛 값을 덮는다
- pipeline: run_transform·run_transform_with_result 가 _route_of(결과)를 넘긴다 (mock 경로는 없음)

DB 격리는 conftest 의 isolated_db, 옛 스키마 테스트는 tmp_path 로 따로.
"""
import sqlite3

import pytest

import app.core.tracing as tracing
import app.services.pipeline as pipeline_mod
from app.core import db
from app.core.config import settings
from app.services.persistence import store

ROUTE = {"mode": "composite", "composite_reason": "document",
         "photo_type": "document", "wear_level": "light"}


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)


def _cols(table="results"):
    with db.get_conn() as c:
        return {r["name"]: r["type"] for r in c.execute(f"PRAGMA table_info({table})")}


def _route(row):
    return {k: row[k] for k in db.RESULT_ROUTE_COLS}


def _record(fid="f", preset="p", route=None, **kw):
    store.record_result(fid, preset, f"{fid}_{preset}.jpg", "mug", ["a"], True,
                        elapsed_s=1.0, route=route, **kw)
    return store.get_result(fid, preset)


_LEGACY_RESULTS = """
    CREATE TABLE results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        file_id TEXT NOT NULL, preset_key TEXT NOT NULL, result_name TEXT NOT NULL,
        item TEXT, considered TEXT, gate_passed INTEGER, bubbles TEXT, elapsed_s REAL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(file_id, preset_key))"""


def _legacy_db(monkeypatch, tmp_path, extra_cols=()):
    path = tmp_path / "legacy_results.db"
    monkeypatch.setattr(settings, "db_path", str(path))
    conn = sqlite3.connect(str(path))
    conn.execute(_LEGACY_RESULTS)
    for col in extra_cols:
        conn.execute(f"ALTER TABLE results ADD COLUMN {col} TEXT")
    conn.execute("INSERT INTO results(file_id, preset_key, result_name, item, gate_passed) "
                 "VALUES ('old', 'p', 'old_p.jpg', 'chair', 1)")
    conn.commit()
    conn.close()
    return path


def test_migrate_adds_route_columns_to_legacy_results_table(monkeypatch, tmp_path):
    _legacy_db(monkeypatch, tmp_path)
    assert not set(db.RESULT_ROUTE_COLS) & set(_cols())

    db.init_db()

    cols = _cols()
    for col in db.RESULT_ROUTE_COLS:
        assert cols.get(col) == "TEXT", col
    # 기존 행은 그대로, 새 컬럼은 NULL
    row = store.get_result("old", "p")
    assert row["item"] == "chair" and row["gate_passed"] == 1
    assert _route(row) == {k: None for k in db.RESULT_ROUTE_COLS}


def test_migrate_adds_only_missing_route_columns(monkeypatch, tmp_path):
    """일부만 있는(중간에 멈춘) DB — 빠진 것만 붙인다."""
    _legacy_db(monkeypatch, tmp_path, extra_cols=("mode", "photo_type"))
    db.init_db()
    assert set(db.RESULT_ROUTE_COLS) <= set(_cols())


# ── store.record_result(route=...) ──
def test_record_result_stores_route():
    assert _route(_record(route=ROUTE)) == ROUTE


@pytest.mark.parametrize("second", [None, {}, {"mode": "generate"}])
def test_record_result_upsert_overwrites_old_route_with_null(second):
    """재변환 결과에 없는 키는 NULL 로 덮는다 — 옛 경로가 새 결과에 남지 않게."""
    _record(route=ROUTE)
    row = _record(route=second)
    expected = {k: (second or {}).get(k) for k in db.RESULT_ROUTE_COLS}
    assert _route(row) == expected
    assert row["composite_reason"] is None and row["photo_type"] is None


class _FakeGraph:
    def __init__(self, out):
        self.out = out

    def invoke(self, state, config=None):
        return self.out


def _graph_out(**kw):
    return {"result_name": "g_p.jpg", "prompt_used": "x", "checks": [], "bubbles": [],
            "gate_passed": None, "item": "book", "considered": [], **kw}


@pytest.mark.parametrize("route", [
    ROUTE,
    {"mode": "original", "composite_reason": "inside_view", "photo_type": "inside_view",
     "wear_level": "none"},
    {"mode": "generate", "composite_reason": None, "photo_type": "product", "wear_level": "light"},
])
def test_run_transform_records_route(monkeypatch, route):
    monkeypatch.setattr(pipeline_mod, "GRAPH", _FakeGraph(_graph_out(**route)), raising=False)
    monkeypatch.setattr(pipeline_mod, "judge_and_save", lambda *a, **k: None)
    pipeline_mod.run_transform("g", "p")
    assert _route(store.get_result("g", "p")) == route


def test_record_failure_with_route_does_not_fail_transform(monkeypatch):
    def boom(*a, **kw):
        raise sqlite3.OperationalError("no such column: photo_type")
    monkeypatch.setattr(pipeline_mod.store, "record_result", boom)
    monkeypatch.setattr(pipeline_mod, "GRAPH", _FakeGraph(_graph_out(**ROUTE)), raising=False)
    monkeypatch.setattr(pipeline_mod, "judge_and_save", lambda *a, **k: None)
    out = pipeline_mod.run_transform("g", "p")
    assert out["photo_type"] == "document"


# ── db._add_column: 동시 기동 경합(duplicate column)만 무시 ──
def _mem():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (id INTEGER)")
    return conn


def test_add_column_duplicate_is_ignored():
    """다른 워커가 PRAGMA 확인 뒤 먼저 추가한 경우 — 실제 sqlite 의 duplicate 오류를 삼킨다."""
    conn = _mem()
    db._add_column(conn, "t", "mode TEXT")
    db._add_column(conn, "t", "mode TEXT")          # 예외 없음
    names = [r["name"] for r in conn.execute("PRAGMA table_info(t)")]
    assert names.count("mode") == 1


def test_migrate_uses_add_column_and_survives_race(monkeypatch, tmp_path):
    """PRAGMA 로 "없음"을 본 뒤 다른 워커가 먼저 붙인 상황 — 첫 ALTER 전에 같은 컬럼을 끼워 넣는다."""
    _legacy_db(monkeypatch, tmp_path)
    real = db._add_column
    seen = []

    def racing(c, table, coldef):
        seen.append((table, coldef))
        if table == "results" and coldef.startswith("mode "):
            c.execute("ALTER TABLE results ADD COLUMN mode TEXT")   # 경쟁 워커
        return real(c, table, coldef)
    monkeypatch.setattr(db, "_add_column", racing)

    db.init_db()

    assert set(db.RESULT_ROUTE_COLS) <= set(_cols())
    assert [cd.split()[0] for t, cd in seen if t == "results"] == list(db.RESULT_ROUTE_COLS)




def test_record_result_leaves_legacy_bubbles_column_untouched():
    """10-05 말풍선 제거: 새 행은 NULL, 재기록해도 옛 bubbles 값은 그대로."""
    from app.core import db
    assert _record("b", "p")["bubbles"] is None
    with db.get_conn() as c:
        c.execute("UPDATE results SET bubbles='[1]' WHERE file_id='b'")
    assert _record("b", "p")["bubbles"] == "[1]"
