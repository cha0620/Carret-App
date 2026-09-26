"""POST /api/transform (응답 뒤 채점) + GET /api/quality/{file_id}/{preset} (폴링).

pipeline.run_transform / judge_and_save 는 가짜로 바꿔치기 — 라우트가 무엇을 넘기고
무엇을 돌려주는지만 본다. TestClient 는 BackgroundTasks 를 응답 직후 같은
스레드에서 실행하므로 judge_and_save 호출 여부를 바로 확인할 수 있다.
"""
import json

import pytest

FID = "0123456789abcdef0123456789abcdef"


def _out(**kw):
    return {
        "result_name": f"{FID}_studio_white.jpg",
        "prompt_used": "t",
        "checks": [],
        "bubbles": [],
        "gate_passed": None,
        "item": "mug",
        "considered": [],
        **kw,
    }


@pytest.fixture()
def fake_pipeline(monkeypatch):
    """run_transform/judge_and_save 호출 기록. rec["out"] 을 바꿔 반환값을 조절."""
    rec = {"run": [], "later": [], "out": _out()}

    def run_transform(fid, preset, **kw):
        rec["run"].append((fid, preset, kw))
        return rec["out"]

    def judge_and_save(*a, **kw):
        rec["later"].append((a, kw))

    monkeypatch.setattr("app.services.pipeline.run_transform", run_transform)
    monkeypatch.setattr("app.services.pipeline.judge_and_save", judge_and_save)
    return rec


def _post(client, preset="studio_white"):
    return client.post("/api/transform", json={"file_id": FID, "preset": preset})


# ── POST /api/transform ──
def test_transform_requests_deferred_judge(client, fake_pipeline):
    assert _post(client).status_code == 200
    assert fake_pipeline["run"] == [(FID, "studio_white", {"defer_judge": True})]


def test_transform_judge_pending_schedules_background_judge(client, fake_pipeline):
    fake_pipeline["out"] = _out(judge_pending=True, trace_id="tr-1", trace_span_id="sp-1")
    body = _post(client).json()
    assert body["judge_pending"] is True
    # trace_id/parent_span_id 는 키워드 전용 — 위치 인자로 넘기면 TypeError
    assert fake_pipeline["later"] == [
        ((FID, "studio_white"), {"trace_id": "tr-1", "parent_span_id": "sp-1"})]


def test_transform_judge_pending_without_trace_ids(client, fake_pipeline):
    fake_pipeline["out"] = _out(judge_pending=True)
    _post(client)
    assert fake_pipeline["later"] == [
        ((FID, "studio_white"), {"trace_id": None, "parent_span_id": None})]


@pytest.mark.parametrize("extra", [{"judge_pending": False}, {}])
def test_transform_not_pending_no_background_judge(client, fake_pipeline, extra):
    fake_pipeline["out"] = _out(**extra)
    body = _post(client).json()
    assert body["judge_pending"] is False
    assert fake_pipeline["later"] == []


def test_transform_response_new_fields_passthrough(client, fake_pipeline):
    fake_pipeline["out"] = _out(mode="composite", composite_reason="text_heavy",
                                detect_failed=True, status="pass", judge_pending=True)
    body = _post(client).json()
    assert body["mode"] == "composite"
    assert body["composite_reason"] == "text_heavy"
    assert body["detect_failed"] is True
    assert body["status"] == "pass"
    assert body["judge_pending"] is True


def test_transform_response_new_fields_defaults(client, fake_pipeline):
    body = _post(client).json()
    assert body["composite_reason"] is None
    assert body["detect_failed"] is False
    assert body["judge_pending"] is False
    assert body["status"] == "pass"
    assert body["mode"] == "generate"


def test_transform_blocked_status_in_response(client, fake_pipeline):
    fake_pipeline["out"] = _out(status="blocked", mode="composite_failed",
                                composite_reason="guard_failed", judge_pending=False)
    body = _post(client).json()
    assert body["status"] == "blocked" and body["mode"] == "composite_failed"
    assert fake_pipeline["later"] == []


def test_transform_pending_ignores_existing_quality_file(client, fake_pipeline, tmp_storage):
    """채점 대기 중이면 디스크의 성적표는 옛 것(삭제 실패 등) — 응답에 싣지 않는다."""
    (tmp_storage / "quality" / f"{FID}_studio_white.json").write_text(
        json.dumps({"fidelity": 1, "realism": 1, "trust": 1, "analysis": "old"}))
    fake_pipeline["out"] = _out(judge_pending=True)
    assert _post(client).json()["quality"] is None


def test_transform_not_pending_returns_quality_file(client, fake_pipeline, tmp_storage):
    (tmp_storage / "quality" / f"{FID}_studio_white.json").write_text(
        json.dumps({"fidelity": 4, "realism": 3, "trust": 5, "analysis": "ok"}))
    q = _post(client).json()["quality"]
    assert q == {"fidelity": 4, "realism": 3, "trust": 5, "analysis": "ok"}


def test_transform_pipeline_error_is_500_and_no_judge(client, monkeypatch):
    later = []

    def boom(*a, **k):
        raise RuntimeError("fal down")
    monkeypatch.setattr("app.services.pipeline.run_transform", boom)
    monkeypatch.setattr("app.services.pipeline.judge_and_save",
                        lambda *a, **k: later.append((a, k)))
    r = _post(client)
    assert r.status_code == 500 and later == []


def test_transform_background_task_calls_real_judge_and_save_signature(client, monkeypatch):
    """라우트가 넘기는 인자가 실제 judge_and_save 시그니처(키워드 전용)와 맞는지 —
    가짜가 *a/**kw 로 다 받아 주면 위치/키워드 불일치를 못 잡는다."""
    import inspect

    import app.services.pipeline as pipeline_mod
    real_sig = inspect.signature(pipeline_mod.judge_and_save)
    bound = []

    def checked(*a, **kw):
        bound.append(real_sig.bind(*a, **kw))    # 안 맞으면 TypeError
    monkeypatch.setattr("app.services.pipeline.run_transform",
                        lambda f, p, **kw: _out(judge_pending=True, trace_id="t",
                                                trace_span_id="s"))
    monkeypatch.setattr("app.services.pipeline.judge_and_save", checked)
    assert _post(client).status_code == 200
    [b] = bound
    assert b.arguments == {"file_id": FID, "preset_key": "studio_white",
                           "trace_id": "t", "parent_span_id": "s"}


def test_transform_real_pipeline_404_when_original_missing(client, tmp_storage):
    assert _post(client).status_code == 404


# ── GET /api/quality/{file_id}/{preset} ──
def test_quality_missing_is_404(client, tmp_storage):
    r = client.get(f"/api/quality/{FID}/studio_white")
    assert r.status_code == 404


def test_quality_returns_report(client, tmp_storage):
    (tmp_storage / "quality" / f"{FID}_warm_wood.json").write_text(json.dumps(
        {"fidelity": 5, "realism": 4, "trust": 3, "analysis": "좋음"}, ensure_ascii=False),
        encoding="utf-8")
    r = client.get(f"/api/quality/{FID}/warm_wood")
    assert r.status_code == 200
    assert r.json() == {"fidelity": 5, "realism": 4, "trust": 3, "analysis": "좋음"}


def test_quality_partial_report_fills_defaults(client, tmp_storage):
    (tmp_storage / "quality" / f"{FID}_minimal_gray.json").write_text(json.dumps({"fidelity": 2}))
    r = client.get(f"/api/quality/{FID}/minimal_gray")
    assert r.json() == {"fidelity": 2, "realism": 0, "trust": 0, "analysis": ""}


def test_quality_does_not_serve_inspect_file(client, tmp_storage):
    (tmp_storage / "quality" / f"{FID}_studio_white_inspect.json").write_text("{}")
    assert client.get(f"/api/quality/{FID}/studio_white").status_code == 404


def test_quality_reads_through_storage_backend(client, monkeypatch):
    """/storage 정적 마운트가 아니라 storage.load 를 거친다 (S3 모드에서도 보임)."""
    seen = []
    monkeypatch.setattr("app.services.persistence.storage.load",
                        lambda kind, name: seen.append((kind, name)) or
                        b'{"fidelity": 1, "realism": 2, "trust": 3, "analysis": "s3"}')
    r = client.get(f"/api/quality/{FID}/studio_white")
    assert r.status_code == 200 and r.json()["analysis"] == "s3"
    assert seen == [("quality", f"{FID}_studio_white.json")]


@pytest.mark.parametrize("fid", [
    "0123456789ABCDEF0123456789ABCDEF",          # 대문자
    "0123456789abcdef0123456789abcde",           # 31자
    "0123456789abcdef0123456789abcdef0",         # 33자
    "0123456789abcdef0123456789abcdeg",          # hex 아님
    "..%2F..%2Fetc%2Fpasswd",
])
def test_quality_bad_file_id_is_422(client, tmp_storage, fid):
    assert client.get(f"/api/quality/{fid}/studio_white").status_code in (404, 422)
    # 경로 탈출 시도가 라우트에 매칭돼 storage 까지 가지 않는지: 정상 패턴 아닌 건 422
    if "/" not in fid and "%" not in fid:
        assert client.get(f"/api/quality/{fid}/studio_white").status_code == 422


@pytest.mark.parametrize("preset", ["unknown", "STUDIO_WHITE", "studio_white_inspect"])
def test_quality_bad_preset_is_422(client, tmp_storage, preset):
    assert client.get(f"/api/quality/{FID}/{preset}").status_code == 422
