FAKE_OUT = {
    "result_name": "x_studio_white.jpg",
    "prompt_used": "t",
    "checks": [],
    "bubbles": [{"what": "얼룩", "where": "앞면", "preserved": True,
                 "x1": 100, "y1": 100, "x2": 200, "y2": 200}],
    "gate_passed": True,
}


def test_transform_keeps_bubbles(client, tmp_storage, monkeypatch):
    """회귀: bubbles 묵묵 드롭 금지."""
    (tmp_storage / "original" / "x.jpg").write_bytes(b"fake")
    monkeypatch.setattr("app.services.pipeline.run_transform",
                        lambda fid, p: FAKE_OUT)
    body = client.post("/api/transform",
                       json={"file_id": "x", "preset": "studio_white"}).json()
    assert body["bubbles"][0]["x1"] == 100
    assert body["gate_passed"] is True


def test_transform_404(client, tmp_storage):
    r = client.post("/api/transform",
                    json={"file_id": "ghost", "preset": "studio_white"})
    assert r.status_code == 404


def test_transform_422(client):
    assert client.post("/api/transform", json={}).status_code == 422