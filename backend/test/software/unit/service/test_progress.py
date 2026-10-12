"""변환 작업 진행 상황 레지스트리 (progress.py)."""
import pytest

from app.services.pipeline import progress


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.setattr(progress, "_jobs", {})


def test_publish_without_job_is_noop():
    progress.publish(None, "generating", preview=b"x")
    progress.publish("0" * 32, "generating", preview=b"x")
    assert progress._jobs == {}


def test_publish_updates_stage_and_preview():
    j, new = progress.create("f")
    assert new
    progress.publish(j.id, "checking", attempt=2, preview=b"img")
    j = progress.get(j.id)
    assert (j.stage, j.attempt, j.preview) == ("checking", 2, b"img")


def test_clear_preview_drops_rejected_image():
    j, _ = progress.create("f")
    progress.publish(j.id, "checking", preview=b"img")
    progress.publish(j.id, "regenerating", clear_preview=True)
    assert progress.get(j.id).preview is None


@pytest.mark.parametrize("kw", [{"result": {"ok": 1}}, {"error": {"status": 500, "detail": "x"}}])
def test_finish_always_clears_preview_and_ignores_later_publish(kw):
    j, _ = progress.create("f")
    progress.publish(j.id, "checking", preview=b"img")
    progress.finish(j.id, **kw)
    progress.publish(j.id, "generating", preview=b"late")
    j = progress.get(j.id)
    assert j.preview is None and j.stage in progress.DONE


def test_same_photo_reuses_running_job():
    a, new_a = progress.create("f")
    b, new_b = progress.create("f")
    assert a.id == b.id and new_a and not new_b


def test_running_cap_raises_busy(monkeypatch):
    monkeypatch.setattr(progress, "MAX_RUNNING", 2)
    progress.create("a")
    progress.create("b")
    with pytest.raises(progress.Busy):
        progress.create("c")


def test_sweep_expires_done_keeps_running_and_closes_stuck(monkeypatch):
    old, _ = progress.create("a")
    progress.finish(old.id, result={})
    old.updated -= progress.TTL_S + 1
    stuck, _ = progress.create("s")
    stuck.created -= progress.STUCK_S + 1
    running, _ = progress.create("b")
    assert progress.get(old.id) is None
    assert progress.get(stuck.id).stage == "error"
    assert progress.get(running.id).stage == "queued"

    monkeypatch.setattr(progress, "MAX_JOBS", 2)
    progress.create("c")
    assert progress.get(running.id) is not None   # 도는 작업은 상한 때문에 지우지 않는다
