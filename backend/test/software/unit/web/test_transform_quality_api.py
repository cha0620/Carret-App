"""POST /api/transform (응답 뒤 누끼 비교 — judge 성적표는 운영에서 안 부른다).

pipeline.run_transform / judge_and_save / item_signals_and_save 는 가짜로 바꿔치기 — 라우트가 무엇을 넘기고
무엇을 돌려주는지만 본다. TestClient 는 BackgroundTasks 를 응답 직후 같은
스레드에서 실행하므로 백그라운드 호출 여부를 바로 확인할 수 있다.
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


def test_transform_item_signals_run_after_response_and_no_judge(client, fake_pipeline):
    """누끼 비교는 응답 뒤 — 사용자가 오리기(3~43초)를 기다리지 않게. judge 성적표는 운영에서 안 부른다."""
    fake_pipeline["out"] = _out(item_signals_pending=True, trace_id="tr-1", trace_span_id="sp-1")
    r = _post(client)
    assert r.status_code == 200 and "quality" not in r.json()
    assert fake_pipeline["later"] == [
        ("items", (FID, "studio_white"), {"trace_id": "tr-1", "parent_span_id": "sp-1"})]
    [(_, _, kw)] = fake_pipeline["run"]
    assert kw["defer_signals"] is True and not kw.get("score_quality")


def test_transform_pipeline_error_is_500_and_no_background(client, monkeypatch):
    later = []

    def boom(*a, **k):
        raise RuntimeError("fal down")
    monkeypatch.setattr("app.services.pipeline.run_transform", boom)
    monkeypatch.setattr("app.services.pipeline.item_signals_and_save",
                        lambda *a, **k: later.append((a, k)))
    r = _post(client)
    assert r.status_code == 500 and later == []


def test_transform_background_task_matches_real_item_signals_signature(client, monkeypatch):
    """라우트가 넘기는 인자가 실제 item_signals_and_save 시그니처와 맞는지 —
    가짜가 *a/**kw 로 다 받아 주면 위치/키워드 불일치를 못 잡는다."""
    import inspect

    import app.services.pipeline as pipeline_mod
    real_sig = inspect.signature(pipeline_mod.item_signals_and_save)
    bound = []

    def checked(*a, **kw):
        bound.append(real_sig.bind(*a, **kw))    # 안 맞으면 TypeError
    monkeypatch.setattr("app.services.pipeline.run_transform",
                        lambda f, p, **kw: _out(item_signals_pending=True, trace_id="t",
                                                trace_span_id="s"))
    monkeypatch.setattr("app.services.pipeline.item_signals_and_save", checked)
    assert _post(client).status_code == 200
    [b] = bound
    assert b.arguments == {"file_id": FID, "preset_key": "studio_white",
                           "trace_id": "t", "parent_span_id": "s"}


def test_transform_never_passes_answer_count(client, fake_pipeline):
    """정답 개수는 eval 전용 — 앱 요청으로 넣을 수 없다."""
    r = client.post("/api/transform", json={"file_id": FID, "preset": "studio_white", "answer_count": 3})
    assert r.status_code == 200 and "answer_count" not in fake_pipeline["run"][0][2]


@pytest.mark.parametrize("sell", [[], [-1], [20],])   # 구성품 목록은 20개까지 (10-09)
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
