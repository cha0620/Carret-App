"""POST/GET /api/transform/jobs — 결과 먼저, 검사는 뒤에서. run_transform 은 가짜, _run_job 은 동기로."""
import pytest
from fastapi import HTTPException

from app.api.routes import transform as route
from app.services.pipeline import progress

FID = "0123456789abcdef0123456789abcdef"
OUT = {"result_name": f"{FID}_studio_white.jpg", "prompt_used": "t", "checks": [], "bubbles": [],
       "gate_passed": None, "item": "mug", "considered": []}


@pytest.fixture()
def sync_jobs(monkeypatch):
    """스레드 대신 같은 자리에서 _run_job 실행."""
    class T:
        def __init__(self, target, args, daemon): self.t, self.a = target, args
        def start(self): self.t(*self.a)
    monkeypatch.setattr(route.threading, "Thread", T)


def test_job_happy_path_preview_then_same_result_as_sync(client, monkeypatch, sync_jobs):
    seen = {}

    def run_transform(fid, key, **kw):
        jid = kw["job_id"]
        progress.publish(jid, "checking", attempt=1, preview=b"\xff\xd8jpg")
        seen["preview"] = client.get(f"/api/transform/jobs/{jid}/preview")
        seen["status"] = client.get(f"/api/transform/jobs/{jid}").json()
        return dict(OUT)
    monkeypatch.setattr("app.services.pipeline.run_transform", run_transform)

    body = {"file_id": FID, "preset": "studio_white"}
    jid = client.post("/api/transform/jobs", json=body).json()["job_id"]
    assert seen["preview"].status_code == 200 and seen["preview"].content == b"\xff\xd8jpg"
    assert seen["preview"].headers["x-content-type-options"] == "nosniff"
    assert seen["status"]["stage"] == "checking" and seen["status"]["preview_url"]

    r = client.get(f"/api/transform/jobs/{jid}").json()
    assert r["stage"] == "done" and r["error"] is None and r["preview_url"] is None
    assert r["result"] == client.post("/api/transform", json=body).json()
    assert client.get(f"/api/transform/jobs/{jid}/preview").status_code == 404


def test_job_http_exception_becomes_error_stage(client, monkeypatch, sync_jobs):
    def boom(*a, **k):
        raise HTTPException(status_code=422, detail="안 됨")
    monkeypatch.setattr("app.services.pipeline.run_transform", boom)
    jid = client.post("/api/transform/jobs", json={"file_id": FID, "preset": "studio_white"}).json()["job_id"]
    r = client.get(f"/api/transform/jobs/{jid}").json()
    assert r["stage"] == "error" and r["error"] == {"status": 422, "detail": "안 됨"} and r["preview_url"] is None


def test_job_busy_is_429(client, monkeypatch):
    monkeypatch.setattr(progress, "_jobs", {})
    monkeypatch.setattr(progress, "MAX_RUNNING", 0)
    r = client.post("/api/transform/jobs", json={"file_id": FID, "preset": "studio_white"})
    assert r.status_code == 429


def test_job_unknown_and_malformed_id(client):
    assert client.get("/api/transform/jobs/" + "a" * 32).status_code == 404
    assert client.get("/api/transform/jobs/" + "a" * 32 + "/preview").status_code == 404
    assert client.get("/api/transform/jobs/..%2F..%2Fetc").status_code in (404, 422)
    assert client.get("/api/transform/jobs/ZZZ/preview").status_code == 422
