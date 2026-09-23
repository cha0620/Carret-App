"""POST /dev/run-inbox — storage/inbox/ 의 원본들 + 직접 준 이미지 URL들을
파이프라인(mock)+자동 피드백으로 돌리고, 성공분만 inbox/done/ 으로 옮기는지,
실패는 남겨두고 다른 파일/URL은 계속 처리하는지, 프리셋 검증과 동시 호출 잠금이
제대로 동작하는지 검증.
"""
import uuid

import pytest
from fastapi import HTTPException

import app.api.routes.dev as dev_mod
import app.services.ai.auto_feedback as auto_feedback_mod
from app.core import db
from app.core.config import settings
from app.services import pipeline
from app.services.persistence import storage


@pytest.fixture()
def feedback_db(monkeypatch, tmp_path):
    """실제 backend/data/*.db 를 건드리지 않도록 요청마다 격리된 sqlite 파일 사용."""
    db_path = tmp_path / "dev_run_inbox.db"
    monkeypatch.setattr(settings, "db_path", str(db_path))
    db.init_db()
    return db_path


@pytest.fixture(autouse=True)
def mock_pipeline_mode(monkeypatch):
    """run_transform() 이 fal.ai/judge 그래프를 타지 않고 원본을 그대로
    결과로 복사하는 지름길을 쓰도록 강제."""
    monkeypatch.setattr(settings, "pipeline_mode", "mock")


@pytest.fixture(autouse=True)
def mock_auto_feedback(monkeypatch):
    """VLM 호출 없이 고정된 피드백을 돌려주도록."""
    monkeypatch.setattr(
        auto_feedback_mod, "generate_feedback",
        lambda original, result: {"rating": 4, "comment": "괜찮은 결과네요"},
    )


def _is_hex_uuid(s: str) -> bool:
    try:
        return len(s) == 32 and uuid.UUID(s).hex == s
    except (ValueError, AttributeError):
        return False


def test_single_valid_image_is_processed_and_moved_to_done(client, feedback_db, make_png, tmp_storage):
    inbox = tmp_storage / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "photo.jpg").write_bytes(make_png())

    r = client.post("/dev/run-inbox")

    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["source_file"] == "photo.jpg"
    assert _is_hex_uuid(row["file_id"])
    assert "result_url" in row and row["result_url"]
    assert row["rating"] == 4
    assert row["comment"] == "괜찮은 결과네요"
    assert row["source"] == "agent"

    # DB 에도 agent 소스로 저장됨
    from app.services.persistence import store
    saved = store.get_feedback(row["file_id"], "studio_white")
    assert saved is not None
    assert saved["rating"] == 4
    assert saved["comment"] == "괜찮은 결과네요"
    assert saved["source"] == "agent"

    # 원본 파일은 inbox/ 최상위에서 사라지고 inbox/done/ 으로 (file_id 접두어와 함께) 이동
    assert not (inbox / "photo.jpg").exists()
    assert (inbox / "done" / f"{row['file_id']}_photo.jpg").exists()


def test_multiple_images_all_processed_and_moved(client, feedback_db, make_png, tmp_storage):
    inbox = tmp_storage / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    names = ["a.jpg", "b.png", "c.webp"]
    for name in names:
        (inbox / name).write_bytes(make_png())

    r = client.post("/dev/run-inbox")

    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 3
    returned_names = {row["source_file"] for row in body["rows"]}
    assert returned_names == set(names)
    file_ids = {row["file_id"] for row in body["rows"]}
    assert len(file_ids) == 3
    for fid in file_ids:
        assert _is_hex_uuid(fid)

    for row in body["rows"]:
        name = row["source_file"]
        assert not (inbox / name).exists()
        assert (inbox / "done" / f"{row['file_id']}_{name}").exists()


def test_non_image_file_is_skipped_entirely(client, feedback_db, make_png, tmp_storage):
    inbox = tmp_storage / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "notes.txt").write_bytes(b"not an image")
    (inbox / "real.jpg").write_bytes(make_png())

    r = client.post("/dev/run-inbox")

    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["source_file"] == "real.jpg"

    # notes.txt 는 처리 대상이 아니므로 그대로 inbox 최상위에 남는다
    assert (inbox / "notes.txt").exists()
    assert not (inbox / "done" / "notes.txt").exists()
    assert (inbox / "done" / f"{row['file_id']}_real.jpg").exists()


def test_empty_or_missing_inbox_returns_empty_rows(client, feedback_db, tmp_storage):
    inbox = tmp_storage / "inbox"
    assert not inbox.exists()  # isolated_storage 는 inbox/ 를 만들지 않는다

    r = client.post("/dev/run-inbox")

    assert r.status_code == 200
    assert r.json() == {"rows": []}
    # 엔드포인트가 스스로 만들어 둔다
    assert inbox.exists()
    assert (inbox / "done").exists()


def test_failing_item_reports_error_row_and_stays_in_inbox(client, feedback_db, make_png, tmp_storage, monkeypatch):
    inbox = tmp_storage / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "bad.jpg").write_bytes(make_png())
    (inbox / "good.jpg").write_bytes(make_png())

    orig_run_transform = pipeline.run_transform

    def flaky_run_transform(file_id, preset_key):
        if not hasattr(flaky_run_transform, "called"):
            flaky_run_transform.called = True
            raise RuntimeError("boom")
        return orig_run_transform(file_id, preset_key)

    monkeypatch.setattr(pipeline, "run_transform", flaky_run_transform)

    r = client.post("/dev/run-inbox")

    assert r.status_code == 200
    body = r.json()
    rows_by_name = {row["source_file"]: row for row in body["rows"]}
    assert set(rows_by_name) == {"bad.jpg", "good.jpg"}

    bad_row = rows_by_name["bad.jpg"]
    assert set(bad_row) == {"source_file", "file_id", "error"}
    assert bad_row["source_file"] == "bad.jpg"
    assert bad_row["error"] == "boom"
    assert _is_hex_uuid(bad_row["file_id"])
    good_row = rows_by_name["good.jpg"]
    assert "error" not in good_row
    assert _is_hex_uuid(good_row["file_id"])

    # 실패분은 inbox 에 그대로, 성공분만 done 으로
    assert (inbox / "bad.jpg").exists()
    assert not (inbox / "done" / f"{bad_row['file_id']}_bad.jpg").exists()
    assert not (inbox / "good.jpg").exists()
    assert (inbox / "done" / f"{good_row['file_id']}_good.jpg").exists()


def test_single_url_is_processed_and_returns_source_url_row(client, feedback_db, make_png, tmp_storage, monkeypatch):
    async def fake_fetch_image(url):
        return (make_png(), "jpeg")

    monkeypatch.setattr(dev_mod, "fetch_image", fake_fetch_image)

    r = client.post("/dev/run-inbox", json={"urls": ["http://example.com/a.jpg"]})

    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["source_url"] == "http://example.com/a.jpg"
    assert _is_hex_uuid(row["file_id"])
    assert "result_url" in row and row["result_url"]
    assert row["rating"] == 4
    assert row["comment"] == "괜찮은 결과네요"
    assert row["source"] == "agent"
    assert "error" not in row


def test_failing_url_reports_error_row_without_affecting_others(client, feedback_db, make_png, tmp_storage, monkeypatch):
    async def fake_fetch_image(url):
        if url == "http://example.com/bad.jpg":
            raise HTTPException(400, "이미지가 아닙니다")
        return (make_png(), "jpeg")

    monkeypatch.setattr(dev_mod, "fetch_image", fake_fetch_image)

    r = client.post("/dev/run-inbox", json={
        "urls": ["http://example.com/bad.jpg", "http://example.com/good.jpg"],
    })

    assert r.status_code == 200
    body = r.json()
    rows_by_url = {row["source_url"]: row for row in body["rows"]}
    assert set(rows_by_url) == {"http://example.com/bad.jpg", "http://example.com/good.jpg"}

    bad_row = rows_by_url["http://example.com/bad.jpg"]
    assert bad_row["error"] == "이미지가 아닙니다"
    assert "result_url" not in bad_row
    assert _is_hex_uuid(bad_row["file_id"])

    good_row = rows_by_url["http://example.com/good.jpg"]
    assert "error" not in good_row
    assert "result_url" in good_row and good_row["result_url"]


def test_mixed_local_files_and_urls_both_processed(client, feedback_db, make_png, tmp_storage, monkeypatch):
    inbox = tmp_storage / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "local.jpg").write_bytes(make_png())

    async def fake_fetch_image(url):
        return (make_png(), "jpeg")

    monkeypatch.setattr(dev_mod, "fetch_image", fake_fetch_image)

    r = client.post("/dev/run-inbox", json={"urls": ["http://example.com/remote.jpg"]})

    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 2

    local_rows = [row for row in body["rows"] if "source_file" in row]
    url_rows = [row for row in body["rows"] if "source_url" in row]
    assert len(local_rows) == 1
    assert len(url_rows) == 1
    assert local_rows[0]["source_file"] == "local.jpg"
    assert url_rows[0]["source_url"] == "http://example.com/remote.jpg"

    fid = local_rows[0]["file_id"]
    assert (inbox / "done" / f"{fid}_local.jpg").exists()


def test_unknown_preset_returns_400_and_does_not_touch_inbox(client, feedback_db, make_png, tmp_storage):
    inbox = tmp_storage / "inbox"
    inbox.mkdir(parents=True, exist_ok=True)
    (inbox / "untouched.jpg").write_bytes(make_png())

    r = client.post("/dev/run-inbox", json={"preset": "nope"})

    assert r.status_code == 400

    # 파일시스템에 아무 영향도 없어야 함 — 아직 처리 전에 걸러졌으므로
    assert (inbox / "untouched.jpg").exists()
    assert not (inbox / "done").exists()


def test_concurrent_call_is_rejected_with_409(client, feedback_db):
    assert dev_mod._inbox_lock.acquire(blocking=False)
    try:
        r = client.post("/dev/run-inbox")
        assert r.status_code == 409
    finally:
        dev_mod._inbox_lock.release()
