"""app.services.ingest.ingest_and_feedback 엣지 케이스.

원본 저장(storage) → originals 메타데이터 기록(store.record_original) →
파이프라인 실행(pipeline.run_transform, mock 모드) → 자동 피드백
(auto_feedback.generate_feedback, mock) → feedbacks 저장까지 한 번에
태우는 단일 진입점. VLM/fal.ai 호출은 mock, DB/storage 격리는 conftest.py
의 autouse fixture(isolated_db/isolated_storage)가 처리해준다."""
import pytest

import app.services.ai.auto_feedback as auto_feedback_mod
from app.core.config import settings
from app.services import ingest
from app.services.persistence import store


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


def test_ingest_and_feedback_creates_originals_row(make_png):
    fid = "ingest-fid-1"
    ingest.ingest_and_feedback(fid, make_png(), ".jpg", "studio_white",
                                source="inbox", original_name="photo.jpg")

    original_row = store.get_original(fid)
    assert original_row is not None
    assert original_row["file_id"] == fid
    assert original_row["ext"] == ".jpg"
    assert original_row["source"] == "inbox"
    assert original_row["original_name"] == "photo.jpg"


def test_ingest_and_feedback_creates_feedback_row(make_png):
    """originals 뿐 아니라 feedbacks 도 같은 file_id 로 함께 생긴다
    (자동 피드백 에이전트 경로)."""
    fid = "ingest-fid-2"
    ingest.ingest_and_feedback(fid, make_png(), ".jpg", "studio_white")

    feedback_row = store.get_feedback(fid, "studio_white")
    assert feedback_row is not None
    assert feedback_row["rating"] == 4
    assert feedback_row["comment"] == "괜찮은 결과네요"
    assert feedback_row["source"] == "agent"


def test_ingest_and_feedback_passes_through_source_and_original_name_for_url(make_png):
    """dev.py 의 URL 수집 경로가 넘기는 source="inbox_url" / original_name=<url> 이
    그대로 originals 테이블에 저장되는지."""
    fid = "ingest-fid-url"
    url = "http://example.com/x.jpg"

    ingest.ingest_and_feedback(fid, make_png(), ".jpg", "studio_white",
                                source="inbox_url", original_name=url)

    row = store.get_original(fid)
    assert row["source"] == "inbox_url"
    assert row["original_name"] == url


def test_ingest_and_feedback_returns_result_url_and_feedback_fields(make_png):
    fid = "ingest-fid-3"
    out = ingest.ingest_and_feedback(fid, make_png(), ".jpg", "studio_white")

    assert "result_url" in out and out["result_url"]
    assert out["rating"] == 4
    assert out["comment"] == "괜찮은 결과네요"


def test_ingest_and_feedback_default_source_is_inbox(make_png):
    """source 인자를 안 주면 기본값 "inbox"."""
    fid = "ingest-fid-default-source"
    ingest.ingest_and_feedback(fid, make_png(), ".jpg", "studio_white")

    row = store.get_original(fid)
    assert row["source"] == "inbox"
    assert row["original_name"] is None
