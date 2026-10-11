"""POST /api/inpaint · /api/inpaint/pick · GET /api/inpaint/stats.
inpainter.run 은 가짜 (fal 은 유료 — 절대 호출 금지). storage 는 conftest 의 임시 폴더."""
import base64
import io

import pytest
from PIL import Image

from app.services.ai import inpainter
from app.services.persistence import storage, store

FID = "0123456789abcdef0123456789abcdef"


def _jpg():
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (100, 100, 100)).save(buf, "JPEG")
    return buf.getvalue()


def _mask(painted=True):
    m = Image.new("RGBA", (32, 32), (0, 0, 0, 0))
    if painted:
        m.paste((255, 0, 0, 255), (0, 0, 8, 8))
    buf = io.BytesIO()
    m.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


@pytest.fixture()
def photos():
    storage.save("original", f"{FID}.jpg", _jpg())
    storage.save("result", f"{FID}_studio_white.jpg", _jpg())


@pytest.fixture()
def fake_run(monkeypatch):
    rec = {"fail": set(), "calls": []}

    def run(model, base, original, mask, note):
        rec["calls"].append(model)
        if model in rec["fail"]:
            raise RuntimeError("boom")
        return _jpg()
    monkeypatch.setattr(inpainter, "run", run)
    return rec


def _post(client, painted=True, preset="studio_white"):
    return client.post("/api/inpaint", json={"file_id": FID, "preset": preset,
                                             "mask": _mask(painted), "note": "글자 지우기"})


def test_empty_mask_422(client, photos, fake_run):
    assert _post(client, painted=False).status_code == 422
    assert fake_run["calls"] == []


def test_missing_photo_404(client, fake_run):
    assert _post(client).status_code == 404


def test_one_side_fails_returns_other(client, photos, fake_run):
    fake_run["fail"] = {"kontext"}
    body = _post(client).json()
    assert len(body["results"]) == 1 and len(body["failed"]) == 1
    assert "slots" not in body                   # 블라인드 — 모델은 숨김
    [(slot, res)] = body["results"].items()
    assert storage.load("result", f"{FID}_{res['preset']}.jpg") is not None
    revealed = client.post("/api/inpaint/pick", json={"file_id": FID, "round_id": body["round_id"],
                                                      "pick": slot}).json()
    assert revealed["slots"][slot] == "flash"


def test_both_fail_502(client, photos, fake_run):
    fake_run["fail"] = {"flash", "kontext"}
    assert _post(client).status_code == 502


def test_pick_reveals_records_next_and_stats(client, photos, fake_run):
    rid1 = _post(client).json()["round_id"]
    rid2 = _post(client).json()["round_id"]
    _post(client)                                 # 고르지 않은 회차
    r = client.post("/api/inpaint/pick", json={"file_id": FID, "round_id": rid1, "pick": "a"}).json()
    assert r["next"] == f"studio_white-ip{rid1}a" and set(r["slots"].values()) == {"flash", "kontext"}
    n = client.post("/api/inpaint/pick", json={"file_id": FID, "round_id": rid2, "pick": "none"}).json()
    assert n["next"] == "studio_white"
    row = store.get_inpaint(FID, rid1)            # 기록은 DB (10-11 — 피드백의 한 종류)
    assert row["pick"] == "a" and row["picked_model"] == r["slots"]["a"] and row["note"]
    assert storage.load("quality", f"{FID}_ip{rid1}_mask.png")
    stats = client.get("/api/inpaint/stats").json()
    assert stats[r["slots"]["a"]] == 1 and stats["none"] == 1 and stats["undecided"] == 1
    assert client.post("/api/inpaint/pick", json={"file_id": FID, "round_id": "deadbeef",
                                                  "pick": "a"}).status_code == 404


def test_pick_twice_and_failed_side_rejected_key_does_not_grow(client, photos, fake_run):
    """10-11 리뷰: 이미 고른 회차 · 실패한 쪽은 못 고른다, 이어 고쳐도 키는 마지막 회차만."""
    rid = _post(client).json()["round_id"]
    r = client.post("/api/inpaint/pick", json={"file_id": FID, "round_id": rid, "pick": "a"})
    assert r.status_code == 200 and r.json()["next"] == f"studio_white-ip{rid}a"
    assert client.post("/api/inpaint/pick", json={"file_id": FID, "round_id": rid, "pick": "b"}).status_code == 409
    from app.api.routes.inpaint import ROUND_SUFFIX
    assert ROUND_SUFFIX.sub("", f"studio_white-ip{rid}a-ip0123abcdb") == "studio_white"
