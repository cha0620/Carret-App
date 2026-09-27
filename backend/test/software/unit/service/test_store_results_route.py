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


def _count(fid, preset):
    with db.get_conn() as c:
        return c.execute("SELECT COUNT(*) AS n FROM results WHERE file_id=? AND preset_key=?",
                         (fid, preset)).fetchone()["n"]


def _record(fid="f", preset="p", route=None, **kw):
    store.record_result(fid, preset, f"{fid}_{preset}.jpg", "mug", ["a"], True, [],
                        elapsed_s=1.0, route=route, **kw)
    return store.get_result(fid, preset)


# ── 스키마 / 마이그레이션 ──
def test_route_cols_constant():
    assert db.RESULT_ROUTE_COLS == ("mode", "composite_reason", "photo_type", "wear_level")


def test_fresh_db_has_route_columns_as_text():
    cols = _cols()
    for col in db.RESULT_ROUTE_COLS:
        assert cols.get(col) == "TEXT", col


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


def test_migrate_route_columns_is_idempotent(monkeypatch, tmp_path):
    _legacy_db(monkeypatch, tmp_path)
    db.init_db()
    db.init_db()            # 이미 있으면 ALTER 하지 않는다 (duplicate column 오류 없음)
    names = [n for n in _cols() if n in db.RESULT_ROUTE_COLS]
    assert sorted(names) == sorted(db.RESULT_ROUTE_COLS)


def test_migrate_adds_only_missing_route_columns(monkeypatch, tmp_path):
    """일부만 있는(중간에 멈춘) DB — 빠진 것만 붙인다."""
    _legacy_db(monkeypatch, tmp_path, extra_cols=("mode", "photo_type"))
    db.init_db()
    assert set(db.RESULT_ROUTE_COLS) <= set(_cols())


def test_migrate_does_not_touch_route_values_already_stored(monkeypatch, tmp_path):
    path = _legacy_db(monkeypatch, tmp_path, extra_cols=db.RESULT_ROUTE_COLS)
    conn = sqlite3.connect(str(path))
    conn.execute("UPDATE results SET mode='original', photo_type='inside_view' WHERE file_id='old'")
    conn.commit()
    conn.close()
    db.init_db()
    row = store.get_result("old", "p")
    assert row["mode"] == "original" and row["photo_type"] == "inside_view"


def test_record_result_works_on_migrated_legacy_db(monkeypatch, tmp_path):
    _legacy_db(monkeypatch, tmp_path)
    db.init_db()
    row = _record("old", "p", route=ROUTE)          # 기존 행 upsert
    assert _route(row) == ROUTE and _count("old", "p") == 1


# ── store.record_result(route=...) ──
def test_record_result_stores_route():
    assert _route(_record(route=ROUTE)) == ROUTE


@pytest.mark.parametrize("route", [None, {}])
def test_record_result_without_route_is_all_null(route):
    assert _route(_record(route=route)) == {k: None for k in db.RESULT_ROUTE_COLS}


def test_record_result_missing_keys_are_null():
    row = _record(route={"mode": "generate", "wear_level": "none"})
    assert _route(row) == {"mode": "generate", "composite_reason": None,
                          "photo_type": None, "wear_level": "none"}


def test_record_result_ignores_extra_route_keys():
    row = _record(route={**ROUTE, "scene": "partial_view", "status": "blocked"})
    assert _route(row) == ROUTE
    assert "scene" not in row and "status" not in row


def test_record_result_route_explicit_none_values():
    row = _record(route={k: None for k in db.RESULT_ROUTE_COLS})
    assert _route(row) == {k: None for k in db.RESULT_ROUTE_COLS}


def test_record_result_route_is_keyword_with_default_none():
    import inspect
    params = inspect.signature(store.record_result).parameters
    assert params["route"].default is None and params["elapsed_s"].default is None


def test_record_result_legacy_call_without_route_still_works():
    """예전 호출(route 없이, elapsed_s 키워드) 그대로 동작."""
    store.record_result("lg", "p", "lg_p.jpg", "mug", [], None, [], elapsed_s=2.0)
    row = store.get_result("lg", "p")
    assert row["elapsed_s"] == 2.0 and row["mode"] is None


def test_record_result_upsert_overwrites_route():
    _record(route=ROUTE)
    new = {"mode": "original", "composite_reason": "inside_view",
           "photo_type": "inside_view", "wear_level": "heavy"}
    first_id = store.get_result("f", "p")["id"]
    row = _record(route=new)
    assert _route(row) == new and row["id"] == first_id and _count("f", "p") == 1


@pytest.mark.parametrize("second", [None, {}, {"mode": "generate"}])
def test_record_result_upsert_overwrites_old_route_with_null(second):
    """재변환 결과에 없는 키는 NULL 로 덮는다 — 옛 경로가 새 결과에 남지 않게."""
    _record(route=ROUTE)
    row = _record(route=second)
    expected = {k: (second or {}).get(k) for k in db.RESULT_ROUTE_COLS}
    assert _route(row) == expected
    assert row["composite_reason"] is None and row["photo_type"] is None


def test_record_result_route_is_per_preset():
    _record("f", "a", route=ROUTE)
    _record("f", "b", route=None)
    assert _route(store.get_result("f", "a")) == ROUTE
    assert store.get_result("f", "b")["mode"] is None


# ── pipeline → route ──
def test_route_of_picks_four_keys_only():
    out = {**ROUTE, "status": "blocked", "scene": "x", "gate_passed": True}
    assert pipeline_mod._route_of(out) == ROUTE


def test_route_of_missing_keys_are_none():
    assert pipeline_mod._route_of({}) == {k: None for k in db.RESULT_ROUTE_COLS}


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


def test_run_transform_missing_mode_records_generate_default(monkeypatch):
    """결과의 mode 기본값("generate")이 그대로 DB 에 — 그래프가 안 준 나머지는 NULL."""
    monkeypatch.setattr(pipeline_mod, "GRAPH", _FakeGraph(_graph_out()), raising=False)
    monkeypatch.setattr(pipeline_mod, "judge_and_save", lambda *a, **k: None)
    pipeline_mod.run_transform("g", "p")
    assert _route(store.get_result("g", "p")) == {"mode": "generate", "composite_reason": None,
                                                  "photo_type": None, "wear_level": None}


def test_run_transform_rerun_overwrites_route(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "judge_and_save", lambda *a, **k: None)
    monkeypatch.setattr(pipeline_mod, "GRAPH", _FakeGraph(_graph_out(**ROUTE)), raising=False)
    pipeline_mod.run_transform("g", "p")
    monkeypatch.setattr(pipeline_mod, "GRAPH", _FakeGraph(_graph_out(mode="generate")),
                        raising=False)
    pipeline_mod.run_transform("g", "p")
    row = store.get_result("g", "p")
    assert row["mode"] == "generate" and row["photo_type"] is None
    assert row["composite_reason"] is None and _count("g", "p") == 1


def test_run_transform_passes_route_kwarg_to_store(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline_mod.store, "record_result",
                        lambda *a, **kw: seen.append(kw))
    monkeypatch.setattr(pipeline_mod, "GRAPH", _FakeGraph(_graph_out(**ROUTE)), raising=False)
    monkeypatch.setattr(pipeline_mod, "judge_and_save", lambda *a, **k: None)
    pipeline_mod.run_transform("g", "p")
    assert seen[0]["route"] == ROUTE and seen[0]["elapsed_s"] is not None


def test_mock_mode_records_no_route(monkeypatch, make_png):
    from app.services.persistence import storage
    monkeypatch.setattr(settings, "pipeline_mode", "mock", raising=False)
    storage.save("original", "gm.jpg", make_png())
    seen = []
    real = store.record_result
    monkeypatch.setattr(pipeline_mod.store, "record_result",
                        lambda *a, **kw: seen.append(kw) or real(*a, **kw))
    pipeline_mod.run_transform("gm", "p")
    assert seen[0].get("route") is None
    assert _route(store.get_result("gm", "p")) == {k: None for k in db.RESULT_ROUTE_COLS}


def test_record_failure_with_route_does_not_fail_transform(monkeypatch):
    def boom(*a, **kw):
        raise sqlite3.OperationalError("no such column: photo_type")
    monkeypatch.setattr(pipeline_mod.store, "record_result", boom)
    monkeypatch.setattr(pipeline_mod, "GRAPH", _FakeGraph(_graph_out(**ROUTE)), raising=False)
    monkeypatch.setattr(pipeline_mod, "judge_and_save", lambda *a, **k: None)
    out = pipeline_mod.run_transform("g", "p")
    assert out["photo_type"] == "document"


def test_record_result_safe_route_default_none(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline_mod.store, "record_result", lambda *a, **kw: seen.append(kw))
    pipeline_mod._record_result_safe("f", "p", "r.jpg", "x", [], None, [], 1.0)
    assert seen == [{"elapsed_s": 1.0, "route": None}]


def test_run_transform_with_result_records_route(monkeypatch, make_png):
    monkeypatch.setattr(pipeline_mod, "load", lambda s: {
        "original": b"ORIGINAL", "preset": {"prompt": "P", "name": "n", "bg_color": "#fff"}})
    monkeypatch.setattr(pipeline_mod, "analyze", lambda s: {
        "item": "book", "considered": [], "anchors": [], "detect_failed": False,
        "photo_type": "document", "wear_level": "heavy"})
    monkeypatch.setattr(pipeline_mod, "score_similarity", lambda s: {"visual_similarity": 0.5})
    monkeypatch.setattr(pipeline_mod, "verify", lambda s: {"checks": [], "gate_passed": None})
    monkeypatch.setattr(pipeline_mod, "save_inspect", lambda s: {})
    monkeypatch.setattr(pipeline_mod, "judge_and_save", lambda f, p, **kw: None)
    monkeypatch.setattr(pipeline_mod, "finalize", lambda s: {"bubbles": []})

    pipeline_mod.run_transform_with_result("gd", "p", make_png())

    # dev 그래프는 plan 이 사유를 달지 않고 제공 이미지를 쓴다 → mode generate, 사유 NULL
    assert _route(store.get_result("gd", "p")) == {"mode": "generate", "composite_reason": None,
                                                   "photo_type": "document", "wear_level": "heavy"}


# ── db._add_column: 동시 기동 경합(duplicate column)만 무시 ──
def _mem():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE t (id INTEGER)")
    return conn


def test_add_column_adds_column():
    conn = _mem()
    db._add_column(conn, "t", "mode TEXT")
    assert "mode" in {r["name"] for r in conn.execute("PRAGMA table_info(t)")}


def test_add_column_duplicate_is_ignored():
    """다른 워커가 PRAGMA 확인 뒤 먼저 추가한 경우 — 실제 sqlite 의 duplicate 오류를 삼킨다."""
    conn = _mem()
    db._add_column(conn, "t", "mode TEXT")
    db._add_column(conn, "t", "mode TEXT")          # 예외 없음
    names = [r["name"] for r in conn.execute("PRAGMA table_info(t)")]
    assert names.count("mode") == 1


class _FakeConn:
    def __init__(self, exc):
        self.exc = exc
        self.sql = []

    def execute(self, sql, *a):
        self.sql.append(sql)
        raise self.exc


@pytest.mark.parametrize("msg", ["duplicate column name: mode", "DUPLICATE COLUMN NAME: mode"])
def test_add_column_duplicate_message_case_insensitive(msg):
    conn = _FakeConn(sqlite3.OperationalError(msg))
    db._add_column(conn, "results", "mode TEXT")
    assert conn.sql == ["ALTER TABLE results ADD COLUMN mode TEXT"]


@pytest.mark.parametrize("exc", [
    sqlite3.OperationalError("database is locked"),
    sqlite3.OperationalError("no such table: results"),
    sqlite3.DatabaseError("file is not a database"),
    RuntimeError("boom"),
])
def test_add_column_other_errors_propagate(exc):
    with pytest.raises(type(exc)):
        db._add_column(_FakeConn(exc), "results", "mode TEXT")


def test_add_column_missing_table_propagates_real_sqlite():
    with pytest.raises(sqlite3.OperationalError, match="no such table"):
        db._add_column(_mem(), "nope", "mode TEXT")


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


def test_migrate_other_alter_error_propagates(monkeypatch, tmp_path):
    _legacy_db(monkeypatch, tmp_path)

    def boom(c, table, coldef):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(db, "_add_column", boom)
    with pytest.raises(sqlite3.OperationalError, match="locked"):
        db.init_db()
