"""공개 데모 비용 상한 (app/core/quota.py, 11-15)."""
import pytest

from app.core import quota
from app.core.config import settings

FID = "0123456789abcdef0123456789abcdef"
FAKE_OUT = {"result_name": "x_studio_white.jpg", "prompt_used": "t", "checks": [],
            "gate_passed": True, "item": "hoodie", "considered": []}


@pytest.fixture()
def quota_on(monkeypatch):
    monkeypatch.setattr(settings, "quota_enabled", True)
    monkeypatch.setattr(settings, "quota_daily_usd", 1.0)
    monkeypatch.setattr(settings, "quota_per_ip_daily_usd", 0.1)
    monkeypatch.setattr(settings, "trust_proxy_headers", False)


@pytest.mark.parametrize("method,path,paid", [
    ("POST", "/api/transform", True),
    ("POST", "/api/analyze", True),
    ("POST", "/api/components", True),
    ("POST", "/api/items", True),
    ("POST", "/api/items/abc/files", True),
    ("POST", "/api/items/abc/review", True),
    ("POST", "/api/inpaint", True),
    ("POST", "/api/inpaint/pick", False),   # 고르기만 — 모델 호출 없음
    ("POST", "/api/feedback", False),
    ("GET", "/api/transform", False),
    ("GET", "/api/items/abc", False),
    ("POST", "/api/images/upload", False),
])
def test_cost_table(method, path, paid):
    assert (quota.cost_of(method, path) > 0) is paid


def test_per_ip_limit_then_other_ip_ok(quota_on):
    assert quota.try_reserve("1.1.1.1", 0.06, day="2026-11-15") is None
    assert quota.try_reserve("1.1.1.1", 0.06, day="2026-11-15") == "ip"     # 0.12 > 0.1
    assert quota.try_reserve("2.2.2.2", 0.06, day="2026-11-15") is None


def test_daily_limit_across_ips(quota_on, monkeypatch):
    monkeypatch.setattr(settings, "quota_per_ip_daily_usd", 10.0)
    assert quota.try_reserve("a", 0.6, day="2026-11-15") is None
    assert quota.try_reserve("b", 0.6, day="2026-11-15") == "daily"         # 전체 1.2 > 1.0
    assert quota.try_reserve("b", 0.6, day="2026-11-16") is None            # 다음 날은 새로


def test_rejected_request_is_not_counted(quota_on):
    quota.try_reserve("1.1.1.1", 0.06, day="2026-11-15")
    quota.try_reserve("1.1.1.1", 0.06, day="2026-11-15")                    # 막힘 — 더하지 않는다
    assert quota.usage_today("2026-11-15")["usd"] == pytest.approx(0.06)


def test_middleware_returns_429_with_korean_message(client, tmp_storage, quota_on, monkeypatch):
    (tmp_storage / "original" / f"{FID}.jpg").write_bytes(b"fake")
    called = []
    monkeypatch.setattr("app.services.pipeline.run_transform",
                        lambda fid, p, **kw: called.append(1) or dict(FAKE_OUT))
    monkeypatch.setattr(settings, "quota_per_ip_daily_usd", 0.05)           # 변환(0.04) 한 번만
    body = {"file_id": FID, "preset": "studio_white"}
    assert client.post("/api/transform", json=body).status_code != 429
    r = client.post("/api/transform", json=body)
    assert r.status_code == 429
    assert r.json()["quota"] == "ip" and "내일" in r.json()["detail"]
    assert len(called) == 1                                                  # 막힌 요청은 파이프라인까지 안 간다


def test_disabled_by_default_counts_nothing(client):
    assert settings.quota_enabled is False
    client.post("/api/analyze", json={})                                     # 422 여도 상관없이 — 세지 않는다
    assert quota.usage_today()["calls"] == 0


def _req(headers, host="10.0.0.1"):
    return type("R", (), {"headers": headers, "client": type("C", (), {"host": host})()})()


def test_proxy_header_only_when_trusted(quota_on, monkeypatch):
    r = _req({"cf-connecting-ip": "9.9.9.9", "x-forwarded-for": "8.8.8.8, 10.0.0.1"})
    assert quota.client_ip(r) == "10.0.0.1"
    monkeypatch.setattr(settings, "trust_proxy_headers", True)
    assert quota.client_ip(r) == "9.9.9.9"
    # x-forwarded-for 맨 왼쪽은 클라이언트가 정할 수 있다 — 믿지 않고 접속 주소로
    assert quota.client_ip(_req({"x-forwarded-for": "8.8.8.8, 10.0.0.1"})) == "10.0.0.1"
    assert quota.client_ip(_req({"cf-connecting-ip": "x" * 500})) == "unknown"     # 아무 문자열이나 DB 키로 쌓지 않는다
    # IPv6 는 /64 로 묶는다
    a = quota.client_ip(_req({"cf-connecting-ip": "2001:db8:1:2::1"}))
    b = quota.client_ip(_req({"cf-connecting-ip": "2001:db8:1:2::ffff"}))
    assert a == b == "2001:db8:1:2::/64"


def test_4xx_is_refunded(client, quota_on):
    for _ in range(5):                                                       # 없는 file_id → 404, 0.04 × 5 > 0.1 이어도
        assert client.post("/api/transform", json={"file_id": FID, "preset": "studio_white"}).status_code == 404
    assert quota.usage_today()["usd"] == pytest.approx(0)


def test_db_error_blocks_with_503(client, quota_on, monkeypatch):
    import sqlite3
    def boom(*a, **k):
        raise sqlite3.OperationalError("database is locked")
    monkeypatch.setattr(quota, "try_reserve", boom)
    r = client.post("/api/transform", json={"file_id": FID, "preset": "studio_white"})
    assert r.status_code == 503 and r.json()["quota"] == "error"


def test_healthz_does_not_expose_usage(client, quota_on):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_disabled_never_429_even_with_zero_limit(client, tmp_storage, monkeypatch):
    """꺼져 있으면 상한이 0 이어도 유료 경로를 막지 않는다 (로컬 · eval 영향 없음)."""
    (tmp_storage / "original" / f"{FID}.jpg").write_bytes(b"fake")
    monkeypatch.setattr("app.services.pipeline.run_transform", lambda fid, p, **kw: dict(FAKE_OUT))
    monkeypatch.setattr(settings, "quota_daily_usd", 0.0)
    monkeypatch.setattr(settings, "quota_per_ip_daily_usd", 0.0)
    body = {"file_id": FID, "preset": "studio_white"}
    assert [client.post("/api/transform", json=body).status_code for _ in range(2)].count(429) == 0


def test_init_db_adds_usage_table_to_existing_db_file(monkeypatch, tmp_path, quota_on):
    """usage 테이블이 생기기 전 만든 DB 파일(배포 볼륨)에도 init_db 가 테이블을 만든다."""
    import sqlite3
    from app.core import db
    path = tmp_path / "old.db"
    old = sqlite3.connect(str(path))
    old.execute("CREATE TABLE feedbacks (id INTEGER PRIMARY KEY, file_id TEXT NOT NULL, preset_key TEXT NOT NULL, "
                "rating INTEGER NOT NULL, comment TEXT, created_at TIMESTAMP, updated_at TIMESTAMP, "
                "UNIQUE(file_id, preset_key))")
    old.commit()
    old.close()
    monkeypatch.setattr(settings, "db_path", str(path))
    db.init_db()
    assert quota.try_reserve("1.1.1.1", 0.05, day="2026-11-15") is None
    assert quota.usage_today("2026-11-15")["calls"] == 1
