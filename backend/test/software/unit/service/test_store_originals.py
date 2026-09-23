"""app.services.persistence.store 의 record_original/get_original 엣지 케이스.

db.init_db() 가 만드는 `originals` 테이블(UNIQUE(file_id))과
ON CONFLICT(file_id) DO NOTHING 동작을 검증한다. DB 격리는 conftest.py의
autouse `isolated_db` fixture(매 테스트마다 tmp db)가 이미 처리해준다."""
from app.services.persistence import store

FID = "0123456789abcdef0123456789abcdef"


def test_record_then_get_original_roundtrip():
    store.record_original(FID, ".jpg", "upload", original_name="a.jpg", size_bytes=123)
    row = store.get_original(FID)

    assert row["file_id"] == FID
    assert row["ext"] == ".jpg"
    assert row["source"] == "upload"
    assert row["original_name"] == "a.jpg"
    assert row["size_bytes"] == 123
    assert row["created_at"] is not None


def test_get_original_missing_returns_none():
    assert store.get_original("nope") is None


def test_record_original_second_call_same_file_id_does_not_overwrite():
    """UNIQUE(file_id) + ON CONFLICT DO NOTHING — 재시도 등으로 같은 file_id를
    두 번 기록해도 첫 호출 값이 그대로 유지되어야 한다."""
    store.record_original(FID, ".jpg", "upload", original_name="first.jpg", size_bytes=10)
    first = store.get_original(FID)

    # 서로 다른 값으로 재호출 — 필드가 하나라도 바뀌면 안 됨
    store.record_original(FID, ".png", "inbox", original_name="second.png", size_bytes=999)
    second = store.get_original(FID)

    assert second == first
    assert second["ext"] == ".jpg"
    assert second["source"] == "upload"
    assert second["original_name"] == "first.jpg"
    assert second["size_bytes"] == 10


def test_record_original_optional_fields_default_none():
    store.record_original("no-optional-fid", ".webp", "upload")
    row = store.get_original("no-optional-fid")
    assert row["original_name"] is None
    assert row["size_bytes"] is None


def test_record_original_distinct_file_ids_are_independent():
    store.record_original("fid-a", ".jpg", "upload", original_name="a.jpg", size_bytes=1)
    store.record_original("fid-b", ".png", "inbox", original_name="b.png", size_bytes=2)

    a = store.get_original("fid-a")
    b = store.get_original("fid-b")
    assert a["source"] == "upload"
    assert b["source"] == "inbox"
    assert a["file_id"] != b["file_id"]
