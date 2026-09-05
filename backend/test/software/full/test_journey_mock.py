def test_journey_mock(client, tmp_storage, monkeypatch, make_png):
    monkeypatch.setattr("app.core.config.settings.pipeline_mode", "mock")
    # ⭐ 생성까지 모의 (fal 호출 차단)
    monkeypatch.setattr("app.services.generator._generate_ai",
                        lambda b, p: make_png())
    fid = client.post("/api/images/upload",
                      files={"file": ("a.png", make_png(), "image/png")}).json()["file_id"]
    r = client.post("/api/transform",
                    json={"file_id": fid, "preset": "studio_white"})
    assert r.status_code == 200
    body = r.json()
    assert client.get(body["result_url"]).status_code == 200
    assert client.get("/").status_code == 200