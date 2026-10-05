import pytest

FAKE_OUT = {
    "result_name": "x_studio_white.jpg",
    "prompt_used": "t",
    "checks": [],
    "gate_passed": True,
    "item": "hoodie",                 
    "considered": ["stain", "tear"],
}


# test_contract.py 상단
FID = "0123456789abcdef0123456789abcdef"   # 패턴에 맞는 32-hex


def test_transform_returns_pipeline_result(client, tmp_storage, monkeypatch):
    (tmp_storage / "original" / f"{FID}.jpg").write_bytes(b"fake")
    monkeypatch.setattr("app.services.pipeline.run_transform",
                        lambda fid, p, **kw: FAKE_OUT)
    body = client.post("/api/transform",
                       json={"file_id": FID, "preset": "studio_white"}).json()
    assert "bubbles" not in body
    assert body["gate_passed"] is True


def test_transform_404(client, tmp_storage):
    r = client.post("/api/transform",
                    json={"file_id": FID, "preset": "studio_white"})
    assert r.status_code == 404    # 이제 진짜 "자원 없음" 404 가 나옴


def test_transform_422(client):
    assert client.post("/api/transform", json={}).status_code == 422

# ── separate (물건마다 따로, 10-05) ──
def _capture_key(monkeypatch):
    seen = {}
    def fake(fid, key, **kw):
        seen["key"] = key
        return FAKE_OUT
    monkeypatch.setattr("app.services.pipeline.run_transform", fake)
    return seen


def test_transform_separate_appends_object_index_to_key(client, tmp_storage, monkeypatch):
    (tmp_storage / "original" / f"{FID}.jpg").write_bytes(b"fake")
    seen = _capture_key(monkeypatch)
    body = client.post("/api/transform", json={"file_id": FID, "preset": "studio_white",
                                               "separate": True, "sell": [2]}).json()
    assert seen["key"].endswith("-o2")
    assert body["preset"] == seen["key"]
    assert "-o2" in body["result_url"]


def test_transform_without_separate_keeps_key(client, tmp_storage, monkeypatch):
    (tmp_storage / "original" / f"{FID}.jpg").write_bytes(b"fake")
    seen = _capture_key(monkeypatch)
    client.post("/api/transform", json={"file_id": FID, "preset": "studio_white", "sell": [2]})
    assert "-o" not in seen["key"]


@pytest.mark.parametrize("sell", [[0, 1], None])
def test_transform_separate_needs_exactly_one_sell(client, sell):
    r = client.post("/api/transform", json={"file_id": FID, "preset": "studio_white",
                                            "separate": True, "sell": sell})
    assert r.status_code == 422
