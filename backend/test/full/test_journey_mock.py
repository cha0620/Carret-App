def test_journey_mock(client, tmp_storage, monkeypatch, make_png):
    monkeypatch.setattr("app.core.config.settings.pipeline_mode", "mock")
    fid = client.post("/api/images/upload",
                      files={"file": ("a.png", make_png(), "image/png")}).json()["file_id"]
    r = client.post("/api/transform",
                    json={"file_id": fid, "preset": "studio_white"})
    assert r.status_code == 200
    body = r.json()
    assert client.get(body["result_url"]).status_code == 200
    assert client.get("/").status_code == 200