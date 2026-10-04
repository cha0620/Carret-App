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
    monkeypatch.setattr("app.services.pipeline.item_signals_and_save",
                        lambda *a, **kw: rec["later"].append(("items", a, kw)))
    return rec


def _post(client, preset="studio_white"):
    return client.post("/api/transform", json={"file_id": FID, "preset": preset})


def test_transform_judge_pending_schedules_background_judge(client, fake_pipeline):
    fake_pipeline["out"] = _out(judge_pending=True, trace_id="tr-1", trace_span_id="sp-1")
    body = _post(client).json()
    assert body["judge_pending"] is True
    # trace_id/parent_span_id 는 키워드 전용 — 위치 인자로 넘기면 TypeError
    assert fake_pipeline["later"] == [
        ((FID, "studio_white"), {"trace_id": "tr-1", "parent_span_id": "sp-1"})]


def test_transform_item_signals_run_after_response_before_judge(client, fake_pipeline):
    """누끼 비교도 응답 뒤 — 사용자가 오리기(3~43초)를 기다리지 않게."""
    fake_pipeline["out"] = _out(judge_pending=True, item_signals_pending=True,
                                trace_id="tr-1", trace_span_id="sp-1")
    assert _post(client).status_code == 200
    kw = {"trace_id": "tr-1", "parent_span_id": "sp-1"}
    assert fake_pipeline["later"] == [("items", (FID, "studio_white"), kw),
                                      ((FID, "studio_white"), kw)]


def test_transform_pending_ignores_existing_quality_file(client, fake_pipeline, tmp_storage):
    """채점 대기 중이면 디스크의 성적표는 옛 것(삭제 실패 등) — 응답에 싣지 않는다."""
    (tmp_storage / "quality" / f"{FID}_studio_white.json").write_text(
        json.dumps({"fidelity": 1, "realism": 1, "trust": 1, "analysis": "old"}))
    fake_pipeline["out"] = _out(judge_pending=True)
    assert _post(client).json()["quality"] is None


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


def test_quality_returns_report(client, tmp_storage):
    (tmp_storage / "quality" / f"{FID}_warm_wood.json").write_text(json.dumps(
        {"fidelity": 5, "realism": 4, "trust": 3, "analysis": "좋음"}, ensure_ascii=False),
        encoding="utf-8")
    r = client.get(f"/api/quality/{FID}/warm_wood")
    assert r.status_code == 200
    assert r.json() == {"fidelity": 5, "realism": 4, "trust": 3, "analysis": "좋음"}


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
    "0123456789abcdef0123456789abcdef0",])
def test_quality_bad_file_id_is_422(client, tmp_storage, fid):
    assert client.get(f"/api/quality/{fid}/studio_white").status_code in (404, 422)
    # 경로 탈출 시도가 라우트에 매칭돼 storage 까지 가지 않는지: 정상 패턴 아닌 건 422
    if "/" not in fid and "%" not in fid:
        assert client.get(f"/api/quality/{fid}/studio_white").status_code == 422


def test_transform_never_passes_answer_count(client, fake_pipeline):
    """정답 개수는 eval 전용 — 앱 요청으로 넣을 수 없다."""
    r = client.post("/api/transform", json={"file_id": FID, "preset": "studio_white", "answer_count": 3})
    assert r.status_code == 200 and "answer_count" not in fake_pipeline["run"][0][2]


@pytest.mark.parametrize("sell", [[], [-1], [12],])
def test_transform_bad_sell_is_422(client, fake_pipeline, sell):
    r = client.post("/api/transform", json={"file_id": FID, "preset": "studio_white", "sell": sell})
    assert r.status_code == 422 and fake_pipeline["run"] == []


def _png():
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (1, 2, 3)).save(buf, format="PNG")
    return buf.getvalue()


def _analysis_saved(**kw):
    from app.services.persistence import storage
    storage.save("original", f"{FID}.png", _png())
    a = {"item": "CD", "photo_type": "product", "wear_level": "light", "text_level": "none",
         "detect_failed": False, **kw}
    storage.save("quality", f"{FID}_analysis.json", json.dumps(a).encode())


def test_analyze_does_not_call_vlm_when_cached(client, monkeypatch):
    _analysis_saved(objects=[], item_count=1)
    monkeypatch.setattr("app.services.pipeline.analyze_original",
                        lambda *a: (_ for _ in ()).throw(AssertionError("VLM 호출")))
    assert client.post("/api/analyze", json={"file_id": FID}).status_code == 200


def test_analyze_fresh_runs_detector_and_returns_objects(client, monkeypatch):
    from app.services.ai import detector
    from app.services.persistence import storage
    storage.save("original", f"{FID}.png", _png())
    monkeypatch.setattr(detector, "analyze", lambda img: {
        "item": "CD", "item_count": 2, "photo_type": "product", "wear_level": "none", "text_level": "none",
        "objects": [{"what": "CD", "box": {"x1": 1, "y1": 1, "x2": 9, "y2": 9}, "for_sale": True}]})
    body = client.post("/api/analyze", json={"file_id": FID}).json()
    assert body["item_count"] == 2 and body["objects"][0]["index"] == 0
