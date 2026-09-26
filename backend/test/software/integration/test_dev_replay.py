ANCHORS = [{"what": "얼룩", "where": "앞면"}]
CHECKS = [{"what": "얼룩", "where": "앞면", "preserved": True,
           "x1": 10, "y1": 10, "x2": 20, "y2": 20}]


def test_verify_replay(client, tmp_storage, monkeypatch, make_png):
    """실 오케스트레이션 + VLM 만 모의 → 체크포인트 리플레이."""
    monkeypatch.setattr("app.services.ai.detector.detect_full",
                        lambda *a, **k: {"anchors": ANCHORS, "text_level": "simple",
                                         "item_box": None})
    monkeypatch.setattr("app.services.ai.detector.verify_and_locate",
                    lambda *a, **k: CHECKS)
    monkeypatch.setattr("app.services.pipeline._generate_ai",
                        lambda b, p: make_png())
    monkeypatch.setattr("app.services.ai.judge.judge", lambda a, b: None)
    # 나머지 VLM 호출도 모의 — 안 막으면 .env 의 실제 키로 Gemini 를 부른다
    monkeypatch.setattr("app.services.ai.detector.classify",
                        lambda *a, **k: {"item": "object", "considered": []})
    monkeypatch.setattr("app.services.ai.detector.check_photo",
                        lambda *a, **k: {"valid": True, "reason": ""})
    monkeypatch.setattr("app.services.ai.detector.read_item_text",
                        lambda *a, **k: {"item_box": None, "texts": []})

    fid = client.post("/api/images/upload",
                      files={"file": ("a.png", make_png(), "image/png")}).json()["file_id"]
    body = client.post("/api/transform",
                       json={"file_id": fid, "preset": "studio_white"}).json()
    assert body["gate_passed"] is True

    r = client.post("/dev/verify",
                    json={"file_id": fid, "preset": "studio_white"})
    assert r.status_code == 200
    assert r.json()["bubbles"]