"""POST /api/items · POST /api/items/{item_id}/files · PUT /api/items/{item_id}/objects · GET /api/items/{item_id}
(여러 각도 업로드 · 10-04 물건별 묶음).

detector.group_objects 와 video.extract_frames 는 가짜로 바꿔치기 — 실제 VLM 호출·동영상 디코딩 없이
라우트가 무엇을 검사·저장·반환하는지만 본다. storage 는 test/conftest.py 의 isolated_storage(tmp).
"""
import io
import json

import pytest
from PIL import Image

from app.core.config import settings
from app.services import listing, video

UNKNOWN = "0123456789abcdef0123456789abcdef"


def _png(color=(120, 90, 60), size=(64, 64)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, color).save(buf, format="PNG")
    return buf.getvalue()


def _jpeg(color=(10, 200, 30)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (48, 48), color).save(buf, format="JPEG")
    return buf.getvalue()


def _files(*named):
    """("a.png", bytes) ... → TestClient multipart files 목록."""
    return [("files", (name, data, "application/octet-stream")) for name, data in named]


@pytest.fixture()
def views(monkeypatch):
    """group_objects 가짜 (물건 하나에 사진 전부 — 예전 classify_views 답과 같은 모양). rec["photos"] 로 사진별 답을, rec["fail"] 로 예외를 조절.
    rec["calls"] 에는 매 호출의 이미지 목록."""
    rec = {"calls": [], "category": "other", "item": "mug", "photos": None, "fail": False}

    def fake(images):
        rec["calls"].append(list(images))
        if rec["fail"]:
            raise RuntimeError("VLM 장애")
        if rec.get("raw") is not None:                     # 묶음 응답을 그대로 (여러 물건 시험)
            return listing.normalize(rec["raw"], len(images))
        photos = rec["photos"] or [{"view": None, "occluded": False, "blurry": False,
                                    "item_visible": True}] * len(images)
        obj = {"id": "o1", "kind": "product", "proof_type": None, "proof_for": None, "name": rec["item"],
               "label": rec["item"], "desc": "", "category": rec["category"], "for_sale": True}
        return {"objects": [obj], "photos": [{**ph, "object": "o1"} for ph in photos[:len(images)]]}
    monkeypatch.setattr("app.services.ai.detector.group_objects", fake)
    return rec


@pytest.fixture()
def frames(monkeypatch):
    """extract_frames 가짜. rec["n"] 장의 PNG 를 돌려주고 받은 인자를 기록. rec["error"] 면 VideoError."""
    rec = {"calls": [], "n": 2, "error": None}

    def fake(data, suffix=".mp4", max_frames=8):
        rec["calls"].append({"size": len(data), "suffix": suffix, "max_frames": max_frames})
        if rec["error"]:
            raise video.VideoError(rec["error"])
        # 실제 extract_frames 처럼 무손실 PNG 를 돌려준다 (저장할 때 한 번만 JPEG 로)
        return [_png((i * 40, 0, 0)) for i in range(min(rec["n"], max_frames))]
    monkeypatch.setattr(video, "extract_frames", fake)
    return rec


def _v(view, **kw):
    return {"view": view, "occluded": False, "blurry": False, "item_visible": True, **kw}


def _originals(root):
    return sorted(p.name for p in (root / "original").iterdir())


def _items(root):
    d = root / "items"
    return sorted(p.name for p in d.iterdir()) if d.exists() else []


# ── POST /api/items ─────────────────────────
def test_create_item_saves_photos_and_item(client, views, isolated_storage):
    views["photos"] = [_v("front"), _v("back")]
    r = client.post("/api/items", files=_files(("a.png", _png()), ("b.jpg", _jpeg())))
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["item_id"]) == 32
    assert body["item"] == "mug" and body["category"] == "other"
    assert [p["view"] for p in body["photos"]] == ["front", "back"]
    assert [p["view_label"] for p in body["photos"]] == ["정면", "뒷면"]
    assert all(p["source"] == "photo" for p in body["photos"])
    fids = [p["file_id"] for p in body["photos"]]
    assert len(set(fids)) == 2
    assert [p["url"] for p in body["photos"]] == [f"/storage/original/{f}.jpg" for f in fids]
    assert body["missing"] == [] and body["retake"] == []
    assert body["complete"] is True and body["views_failed"] is False
    # 저장 확인 — 원본 2장 · 묶음 JSON 1개
    assert _originals(isolated_storage) == sorted(f"{f}.jpg" for f in fids)
    assert _items(isolated_storage) == [f"{body['item_id']}.json"]
    # 분류는 1회, 저장된(정규화된) 원본 바이트를 업로드 순서대로
    assert len(views["calls"]) == 1 and len(views["calls"][0]) == 2
    assert all(b[:2] == b"\xff\xd8" for b in views["calls"][0])


def test_create_item_records_original_metadata(client, views):
    from app.core import db
    r = client.post("/api/items", files=_files(("cup.png", _png())))
    fid = r.json()["photos"][0]["file_id"]
    with db.get_conn() as conn:
        row = conn.execute("SELECT source, original_name FROM originals WHERE file_id=?", (fid,)).fetchone()
    assert tuple(row) == ("item_photo", "cup.png")


def test_create_item_reports_missing_views(client, views):
    views["category"] = "shoes"
    views["photos"] = [_v("front"), _v("side")]
    body = client.post("/api/items", files=_files(("a.png", _png()), ("b.png", _png()))).json()
    assert [m["view"] for m in body["missing"]] == ["back", "bottom"]
    assert body["missing"][0]["label"] == "뒷면" and body["missing"][0]["hint"]
    assert body["complete"] is False


def test_create_item_retake_uses_file_id(client, views):
    views["photos"] = [_v("front"), _v("back", blurry=True), _v("back")]
    body = client.post("/api/items", files=_files(*[(f"{i}.png", _png()) for i in range(3)])).json()
    assert body["retake"] == [{"file_id": body["photos"][1]["file_id"], "reason": "흐려요"}]
    assert body["missing"] == [] and body["complete"] is False
    assert body["photos"][1]["blurry"] is True


def test_create_item_unknown_view_label_is_none(client, views):
    body = client.post("/api/items", files=_files(("a.png", _png()))).json()
    assert body["photos"][0]["view"] is None and body["photos"][0]["view_label"] is None


def test_create_item_classify_failure(client, views, isolated_storage):
    """분류 실패 — 저장은 하되 빠진 면을 모르니 missing 은 비우고 완료로 치지 않는다."""
    views["fail"] = True
    r = client.post("/api/items", files=_files(("a.png", _png()), ("b.png", _png())))
    assert r.status_code == 200
    body = r.json()
    assert body["views_failed"] is True
    assert body["missing"] == [] and body["retake"] == [] and body["complete"] is False
    assert all(p["view"] is None and p["item_visible"] is True for p in body["photos"])
    assert len(_originals(isolated_storage)) == 2
    saved = json.loads((isolated_storage / "items" / f"{body['item_id']}.json").read_text())
    assert saved["views_failed"] is True


@pytest.mark.parametrize("name", ["a.gif", "a.txt", "noext", "a.png.exe"])
def test_create_item_rejects_unsupported_extension(client, views, isolated_storage, name):
    r = client.post("/api/items", files=_files((name, _png())))
    assert r.status_code == 400
    assert "지원하지 않는 형식" in r.json()["detail"]
    assert _originals(isolated_storage) == [] and _items(isolated_storage) == []
    assert views["calls"] == []


def test_create_item_extension_case_insensitive(client, views):
    assert client.post("/api/items", files=_files(("A.PNG", _png()))).status_code == 200


def test_create_item_bad_file_later_saves_nothing(client, views, isolated_storage):
    """앞 파일이 괜찮아도 뒤 파일 검사에서 400 이면 아무것도 저장하지 않는다."""
    r = client.post("/api/items", files=_files(("a.png", _png()), ("b.png", _png()), ("c.bmp", _png())))
    assert r.status_code == 400
    assert _originals(isolated_storage) == [] and _items(isolated_storage) == []


def test_create_item_empty_file_rejected(client, views, isolated_storage):
    r = client.post("/api/items", files=_files(("a.png", _png()), ("b.png", b"")))
    assert r.status_code == 400 and "빈 파일" in r.json()["detail"]
    assert _originals(isolated_storage) == []


def test_create_item_no_files_is_422(client, views):
    assert client.post("/api/items").status_code == 422


def test_create_item_photo_size_limit(client, views, monkeypatch, isolated_storage):
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    big = b"\x00" * (1024 * 1024 + 1)
    r = client.post("/api/items", files=_files(("a.jpg", big)))
    assert r.status_code == 400 and "최대 1MB" in r.json()["detail"]
    assert _originals(isolated_storage) == []


def test_create_item_photo_exactly_at_limit_passes_size_check(client, views, monkeypatch):
    """정확히 상한이면 크기 검사는 통과 — (가짜 이미지라) 그 뒤 디코딩에서 400."""
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    r = client.post("/api/items", files=_files(("a.jpg", b"\x00" * (1024 * 1024))))
    assert r.status_code == 400 and "이미지를 읽을 수 없어요" in r.json()["detail"]


def test_create_item_video_uses_video_size_limit(client, views, frames, monkeypatch):
    monkeypatch.setattr(settings, "max_upload_size_mb", 1)
    monkeypatch.setattr(settings, "max_video_size_mb", 2)
    mid = b"\x00" * (1024 * 1024 + 500)           # 사진 상한은 넘고 영상 상한은 안 넘음
    assert client.post("/api/items", files=_files(("v.mp4", mid))).status_code == 200
    assert frames["calls"][0]["size"] == len(mid)
    r = client.post("/api/items", files=_files(("p.jpg", mid)))
    assert r.status_code == 400 and "최대 1MB" in r.json()["detail"]
    r = client.post("/api/items", files=_files(("v.mov", b"\x00" * (2 * 1024 * 1024 + 1))))
    assert r.status_code == 400 and "최대 2MB" in r.json()["detail"]


@pytest.mark.parametrize("ext", [".mp4", ".mov", ".webm", ".m4v", ".MP4"])
def test_create_item_video_frames_become_photos(client, views, frames, ext, isolated_storage):
    frames["n"] = 3
    r = client.post("/api/items", files=_files((f"clip{ext}", b"fakevideo")))
    assert r.status_code == 200, r.text
    body = r.json()
    assert [p["source"] for p in body["photos"]] == ["video"] * 3
    assert frames["calls"] == [{"size": 9, "suffix": ext.lower(), "max_frames": 8}]
    assert len(_originals(isolated_storage)) == 3


def test_create_item_video_frame_names_recorded(client, views, frames):
    from app.core import db
    body = client.post("/api/items", files=_files(("clip.mp4", b"v"))).json()
    with db.get_conn() as conn:
        rows = [tuple(conn.execute("SELECT source, original_name FROM originals WHERE file_id=?",
                                   (p["file_id"],)).fetchone()) for p in body["photos"]]
    assert rows == [("item_video", "clip.mp4#0"), ("item_video", "clip.mp4#1")]


def test_create_item_mixed_photo_and_video_keeps_order(client, views, frames):
    body = client.post("/api/items", files=_files(("a.png", _png()), ("v.mp4", b"v"),
                                                  ("b.png", _png()))).json()
    assert [p["source"] for p in body["photos"]] == ["photo", "video", "video", "photo"]


def test_create_item_video_error_is_400_and_saves_nothing(client, views, frames, isolated_storage):
    frames["error"] = "동영상이 너무 길어요 — 90초 안으로 찍어 주세요"
    r = client.post("/api/items", files=_files(("a.png", _png()), ("v.mp4", b"v")))
    assert r.status_code == 400 and r.json()["detail"] == frames["error"]
    assert _originals(isolated_storage) == [] and _items(isolated_storage) == []


def test_create_item_video_max_frames_follows_room(client, views, frames, monkeypatch):
    monkeypatch.setattr(settings, "max_item_photos", 5)
    client.post("/api/items", files=_files(("v.mp4", b"v")))
    assert frames["calls"][-1]["max_frames"] == 5


def test_create_item_photo_limit(client, views, monkeypatch, isolated_storage):
    monkeypatch.setattr(settings, "max_item_photos", 3)
    ok = client.post("/api/items", files=_files(*[(f"{i}.png", _png()) for i in range(3)]))
    assert ok.status_code == 200
    n_before = len(_originals(isolated_storage))
    r = client.post("/api/items", files=_files(*[(f"{i}.png", _png()) for i in range(4)]))
    assert r.status_code == 400 and "3장까지" in r.json()["detail"]
    assert len(_originals(isolated_storage)) == n_before


def test_create_item_video_frames_share_remaining_room(client, views, frames, monkeypatch, isolated_storage):
    """영상에서 뽑는 장수는 사진을 뺀 남은 자리까지 — 상한을 넘기지 않고 200."""
    monkeypatch.setattr(settings, "max_item_photos", 3)
    frames["n"] = 8
    r = client.post("/api/items", files=_files(("a.png", _png()), ("v.mp4", b"v")))
    assert r.status_code == 200, r.text
    assert frames["calls"][-1]["max_frames"] == 2
    assert [p["source"] for p in r.json()["photos"]] == ["photo", "video", "video"]


def test_create_item_two_videos_split_room(client, views, frames, monkeypatch):
    monkeypatch.setattr(settings, "max_item_photos", 12)
    client.post("/api/items", files=_files(("a.png", _png()), ("b.png", _png()),
                                           ("v1.mp4", b"v"), ("v2.mov", b"v")))
    assert [c["max_frames"] for c in frames["calls"]] == [5, 5]


def test_create_item_one_video_capped_at_eight(client, views, frames, monkeypatch):
    monkeypatch.setattr(settings, "max_item_photos", 30)
    client.post("/api/items", files=_files(("v.mp4", b"v")))
    assert frames["calls"][-1]["max_frames"] == 8


def test_create_item_too_many_videos_rejected_before_decoding(client, views, frames, isolated_storage):
    r = client.post("/api/items", files=_files(("1.mp4", b"v"), ("2.mp4", b"v"), ("3.webm", b"v")))
    assert r.status_code == 400 and "동영상은 한 번에 2개까지" in r.json()["detail"]
    assert frames["calls"] == [] and _originals(isolated_storage) == []


def test_create_item_too_many_files_rejected_before_reading(client, views, frames, monkeypatch, isolated_storage):
    """장수 초과는 파일을 읽기(디코딩) 전에 거른다 — 내용이 이상해도 장수 오류가 먼저."""
    monkeypatch.setattr(settings, "max_item_photos", 2)
    r = client.post("/api/items", files=_files(("a.png", b"junk"), ("b.png", b"junk"), ("v.mp4", b"v")))
    assert r.status_code == 400 and "2장까지" in r.json()["detail"]
    assert frames["calls"] == [] and _originals(isolated_storage) == []


def test_create_item_corrupt_image_after_good_one_saves_nothing(client, views, isolated_storage):
    """이름만 .png 인 파일이 뒤에 있어도 앞 사진이 원본으로 남지 않는다 (저장 전 검사)."""
    r = client.post("/api/items", files=_files(("a.png", _png()), ("b.png", b"garbage")))
    assert r.status_code == 400 and "이미지를 읽을 수 없어요: b.png" in r.json()["detail"]
    assert _originals(isolated_storage) == [] and _items(isolated_storage) == []
    assert views["calls"] == []


def test_create_item_too_many_pixels_rejected(client, views, monkeypatch, isolated_storage):
    from app.api.routes import items
    monkeypatch.setattr(items, "MAX_PIXELS", 100)
    r = client.post("/api/items", files=_files(("a.png", _png(size=(20, 20)))))
    assert r.status_code == 400 and "너무 커요" in r.json()["detail"]
    assert _originals(isolated_storage) == []


def test_create_item_save_failure_rolls_back(monkeypatch, views, isolated_storage):
    """저장 도중 실패하면 앞서 저장한 원본을 지운다 (반쪽 묶음이 남지 않게)."""
    from fastapi.testclient import TestClient
    from app.services.persistence import storage
    from main import app
    real, n = storage.save, {"original": 0}

    def flaky(kind, name, data):
        if kind == "original":
            n["original"] += 1
            if n["original"] == 3:
                raise OSError("디스크 가득")
        return real(kind, name, data)
    monkeypatch.setattr(storage, "save", flaky)
    c = TestClient(app, raise_server_exceptions=False)
    r = c.post("/api/items", files=_files(*[(f"{i}.png", _png()) for i in range(3)]))
    assert r.status_code == 500
    assert _originals(isolated_storage) == [] and _items(isolated_storage) == []


def test_create_item_corrupt_image_is_400(client, views):
    r = client.post("/api/items", files=_files(("a.png", b"not an image")))
    assert r.status_code == 400 and "이미지를 읽을 수 없어요" in r.json()["detail"]


# ── POST /api/items/{item_id}/files ─────────
def _create(client, n=2):
    return client.post("/api/items", files=_files(*[(f"{i}.png", _png()) for i in range(n)])).json()


def test_add_files_appends_and_reclassifies_all(client, views, isolated_storage):
    first = _create(client, 2)
    views["photos"] = [_v("front"), _v("side"), _v("back")]
    r = client.post(f"/api/items/{first['item_id']}/files", files=_files(("c.png", _png())))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["item_id"] == first["item_id"]
    assert [p["file_id"] for p in body["photos"][:2]] == [p["file_id"] for p in first["photos"]]
    assert len(body["photos"]) == 3
    assert len(views["calls"]) == 2 and len(views["calls"][1]) == 3   # 전부 다시 본다
    assert [p["view"] for p in body["photos"]] == ["front", "side", "back"]
    assert body["complete"] is True
    assert _items(isolated_storage) == [f"{first['item_id']}.json"]


def test_add_files_recovers_from_previous_classify_failure(client, views):
    views["fail"] = True
    first = _create(client, 1)
    assert first["views_failed"] is True
    views["fail"] = False
    views["photos"] = [_v("front"), _v("back")]
    body = client.post(f"/api/items/{first['item_id']}/files", files=_files(("b.png", _png()))).json()
    assert body["views_failed"] is False and body["complete"] is True


def test_add_files_room_limit(client, views, monkeypatch, isolated_storage):
    monkeypatch.setattr(settings, "max_item_photos", 3)
    first = _create(client, 2)
    r = client.post(f"/api/items/{first['item_id']}/files",
                    files=_files(("c.png", _png()), ("d.png", _png())))
    assert r.status_code == 400 and "3장까지" in r.json()["detail"]
    assert len(_originals(isolated_storage)) == 2
    assert len(client.get(f"/api/items/{first['item_id']}").json()["photos"]) == 2
    ok = client.post(f"/api/items/{first['item_id']}/files", files=_files(("c.png", _png())))
    assert ok.status_code == 200 and len(ok.json()["photos"]) == 3


def test_add_files_video_max_frames_is_remaining_room(client, views, frames, monkeypatch):
    monkeypatch.setattr(settings, "max_item_photos", 4)
    first = _create(client, 2)
    frames["n"] = 8
    body = client.post(f"/api/items/{first['item_id']}/files", files=_files(("v.mp4", b"v"))).json()
    assert frames["calls"][-1]["max_frames"] == 2
    assert len(body["photos"]) == 4


def test_add_files_when_full_rejects_before_decoding(client, views, frames, monkeypatch, isolated_storage):
    """남은 자리가 0 이면 영상을 디코딩하지 않고 바로 400."""
    monkeypatch.setattr(settings, "max_item_photos", 2)
    first = _create(client, 2)
    r = client.post(f"/api/items/{first['item_id']}/files", files=_files(("v.mp4", b"v")))
    assert r.status_code == 400 and "2장까지" in r.json()["detail"]
    assert frames["calls"] == []
    assert len(_originals(isolated_storage)) == 2


def test_add_files_classify_failure_keeps_previous_views(client, views):
    """추가 뒤 분류가 실패해도 앞서 분류한 사진의 값은 남고, 새 사진만 모름."""
    views["photos"] = [_v("front"), _v("back", blurry=True)]
    first = _create(client, 2)
    views["fail"] = True
    body = client.post(f"/api/items/{first['item_id']}/files", files=_files(("c.png", _png()))).json()
    assert [p["view"] for p in body["photos"]] == ["front", "back", None]
    assert body["photos"][1]["blurry"] is True
    assert body["views_failed"] is True and body["missing"] == [] and body["complete"] is False
    got = client.get(f"/api/items/{first['item_id']}").json()
    assert got == body


def test_add_files_skips_missing_original_when_classifying(client, views, isolated_storage):
    """원본 파일이 사라진 사진은 VLM 에 넘기지 않고, 남은 사진에 답을 순서대로 맞춘다."""
    first = _create(client, 2)
    lost = first["photos"][0]["file_id"]
    (isolated_storage / "original" / f"{lost}.jpg").unlink()
    views["photos"] = [_v("back"), _v("front")]
    body = client.post(f"/api/items/{first['item_id']}/files", files=_files(("c.png", _png()))).json()
    assert len(views["calls"][-1]) == 2
    assert [p["view"] for p in body["photos"]] == [None, "back", "front"]


def test_add_files_unknown_item_404(client, views, isolated_storage):
    r = client.post(f"/api/items/{UNKNOWN}/files", files=_files(("a.png", _png())))
    assert r.status_code == 404
    assert _originals(isolated_storage) == []


@pytest.mark.parametrize("bad", ["xyz", "0123456789ABCDEF0123456789ABCDEF", UNKNOWN + "0"])
def test_add_files_bad_item_id_422(client, views, bad):
    r = client.post(f"/api/items/{bad}/files", files=_files(("a.png", _png())))
    assert r.status_code == 422


def test_add_files_path_traversal_id_rejected(client, views, isolated_storage):
    """경로 이탈 시도는 어떤 경로로 풀리든 성공하지 않고 아무것도 저장하지 않는다."""
    r = client.post("/api/items/..%2F..%2Fx/files", files=_files(("a.png", _png())))
    assert r.status_code >= 400
    assert _originals(isolated_storage) == [] and _items(isolated_storage) == []


def test_add_files_rejected_file_keeps_item_unchanged(client, views, isolated_storage):
    first = _create(client, 1)
    before = (isolated_storage / "items" / f"{first['item_id']}.json").read_bytes()
    r = client.post(f"/api/items/{first['item_id']}/files", files=_files(("x.gif", _png())))
    assert r.status_code == 400
    assert (isolated_storage / "items" / f"{first['item_id']}.json").read_bytes() == before


# ── GET /api/items/{item_id} ────────────────
def test_get_item_returns_saved_state(client, views):
    views["category"] = "bag"
    views["photos"] = [_v("front", occluded=True)]
    created = client.post("/api/items", files=_files(("a.png", _png()))).json()
    n_calls = len(views["calls"])
    got = client.get(f"/api/items/{created['item_id']}").json()
    assert got == created
    assert len(views["calls"]) == n_calls          # 조회는 다시 분류하지 않는다


def test_get_item_unknown_404(client):
    assert client.get(f"/api/items/{UNKNOWN}").status_code == 404


@pytest.mark.parametrize("bad", ["abc", "0123456789ABCDEF0123456789ABCDEF", "g" * 32])
def test_get_item_bad_id_422(client, bad):
    assert client.get(f"/api/items/{bad}").status_code == 422


def test_get_item_photo_url_is_served(client, views):
    created = _create(client, 1)
    r = client.get(created["photos"][0]["url"])
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8"


# ── EXIF 회전 · 픽셀 상한 (img_util.normalize) ──
def _exif_jpeg(w, h, orientation) -> bytes:
    """픽셀은 w x h 로 누운 채, EXIF Orientation 으로 회전을 적은 휴대폰식 JPEG."""
    img = Image.new("RGB", (w, h), (200, 30, 30))
    img.paste((30, 30, 200), (0, 0, w // 4, h))          # 왼쪽 띠 — 회전 방향 확인용
    exif = Image.Exif()
    exif[0x0112] = orientation
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def test_upload_portrait_exif_photo_stays_upright(client, views, isolated_storage):
    """세로 사진(픽셀 80x40 + Orientation 6)이 저장되며 눕지 않는다 — 40x80 으로 바로 선다."""
    body = client.post("/api/items", files=_files(("portrait.jpg", _exif_jpeg(80, 40, 6)))).json()
    fid = body["photos"][0]["file_id"]
    with Image.open(isolated_storage / "original" / f"{fid}.jpg") as img:
        assert img.size == (40, 80)
        assert img.getexif().get(0x0112) in (None, 1)     # 다시 돌릴 EXIF 가 남지 않는다
        r, g, b = img.convert("RGB").getpixel((20, 5))     # 270° 회전이면 왼쪽 띠가 위로 간다
        assert b > r


def test_upload_exif_photo_sent_upright_to_classifier(client, views):
    client.post("/api/items", files=_files(("portrait.jpg", _exif_jpeg(80, 40, 6))))
    with Image.open(io.BytesIO(views["calls"][0][0])) as img:
        assert img.size == (40, 80)


def test_upload_without_exif_orientation_unchanged(client, views, isolated_storage):
    body = client.post("/api/items", files=_files(("land.jpg", _exif_jpeg(80, 40, 1)))).json()
    with Image.open(isolated_storage / "original" / f"{body['photos'][0]['file_id']}.jpg") as img:
        assert img.size == (80, 40)


def test_normalize_exif_rotation_directly():
    from app.util import img_util
    out = img_util.normalize(_exif_jpeg(80, 40, 8))
    with Image.open(io.BytesIO(out)) as img:
        assert img.size == (40, 80)


def test_normalize_rejects_too_many_pixels(monkeypatch):
    from app.util import img_util
    monkeypatch.setattr(img_util, "MAX_PIXELS", 100)
    with pytest.raises(ValueError, match="너무 큼"):
        img_util.normalize(_png(size=(20, 20)))
    monkeypatch.setattr(img_util, "MAX_PIXELS", 400)
    assert img_util.normalize(_png(size=(20, 20)))[:2] == b"\xff\xd8"     # 정확히 상한이면 통과


# ── 같은 물건에 동시에 추가 (물건별 잠금) ──
def test_add_files_concurrent_requests_keep_all_photos(client, views, monkeypatch):
    """읽고-고치고-쓰는 사이에 다른 요청이 끼어들면 한쪽 사진이 사라진다 — 잠금으로 둘 다 남는다."""
    import threading
    import time
    first = _create(client, 1)
    real = views["calls"]

    from app.services.ai import detector
    fake = detector.group_objects          # views 픽스처의 가짜 (10-04 부터 라우트는 group_objects 를 부른다)

    def slow(images):
        time.sleep(0.2)                     # 분류 중에 다른 요청이 같은 묶음을 읽도록 틈을 둔다
        return fake(images)
    monkeypatch.setattr(detector, "group_objects", slow)
    results = []

    def add(name):
        r = client.post(f"/api/items/{first['item_id']}/files", files=_files((name, _png())))
        results.append(r.status_code)
    ts = [threading.Thread(target=add, args=(f"{i}.png",)) for i in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert results == [200, 200]
    assert len(client.get(f"/api/items/{first['item_id']}").json()["photos"]) == 3
    assert len(real) == 3


# ── 10-01: 정석 구도 (신발) ──────────────────
def test_shoes_item_lists_compositions_with_matching_photos(client, views):
    views["category"] = "shoes"
    views["photos"] = [_v("front_34"), _v("side", occluded=True), _v("side"), _v("bottom")]
    r = client.post("/api/items", files=_files(*[(f"{i}.png", _png((i * 30, 0, 0))) for i in range(4)]))
    body = r.json()
    fids = [p["file_id"] for p in body["photos"]]
    comps = {c["key"]: c for c in body["compositions"]}
    assert list(comps) == ["shoes_front34", "shoes_side", "shoes_top", "shoes_back", "shoes_sole"]
    assert comps["shoes_front34"]["photo_ids"] == [fids[0]]
    assert comps["shoes_side"]["photo_ids"] == [fids[2], fids[1]]        # 가려진 사진은 뒤로
    assert comps["shoes_sole"]["available"] and comps["shoes_sole"]["hint"] is None
    assert not comps["shoes_top"]["available"] and "위에서" in comps["shoes_top"]["hint"]
    assert comps["shoes_side"]["image"] == "/img/compositions/shoes_side.svg"


def test_item_without_compositions_gets_empty_list(client, views):
    views["category"] = "bag"            # 정석 구도가 아직 없는 종류
    views["photos"] = [_v("front")]
    assert client.post("/api/items", files=_files(("a.png", _png()))).json()["compositions"] == []


def test_clothing_item_gets_top_front_composition(client, views):
    views["category"] = "clothing"
    views["photos"] = [_v("front")]
    comps = client.post("/api/items", files=_files(("a.png", _png()))).json()["compositions"]
    assert [c["key"] for c in comps] == ["clothing_top_front"] and comps[0]["available"]


# ── 10-04: 물건별 묶음 (objects · main_object · PUT /objects) ──
def _o(id_, kind="product", **kw):
    base = {"id": id_, "kind": kind, "name": f"thing {id_}", "label": f"물건 {id_}", "desc": "",
            "category": "other" if kind == "product" else None, "for_sale": kind == "product"}
    if kind == "proof":
        base.update(proof_type="document", proof_for=None)
    return {**base, **kw}


def _p(i, obj, view=None, **kw):
    return {"index": i, "object": obj, "view": view, **kw}


SHOES_AND_CARD = {
    "objects": [_o("A", name="sneakers", label="흰 운동화", category="shoes"),
                _o("B", "proof", name="warranty card", label="보증서", proof_for="A")],
    "photos": [_p(0, "A", "front_34"), _p(1, "B", "front"), _p(2, "A", "side")],
}


def _create_raw(client, views, raw, n):
    views["raw"] = raw
    r = client.post("/api/items", files=_files(*[(f"{i}.png", _png((i * 20, 0, 0))) for i in range(n)]))
    assert r.status_code == 200, r.text
    return r.json()


def _edit_body(body, **changes):
    """GET 응답 → PUT 본문 (지금 상태 그대로)."""
    keys = ("id", "kind", "label", "desc", "category", "for_sale", "count", "proof_type", "proof_for")
    out = {"objects": [{k: o[k] for k in keys} for o in body["objects"]],
           "photos": [{"file_id": p["file_id"], "object": p["object"], "view": p["view"]} for p in body["photos"]]}
    out.update(changes)
    return out


def _put(client, body, edit):
    return client.put(f"/api/items/{body['item_id']}/objects", json=edit)


def _saved(root, item_id):
    return json.loads((root / "items" / f"{item_id}.json").read_text())


def test_create_item_groups_objects(client, views, isolated_storage):
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    fids = [p["file_id"] for p in body["photos"]]
    assert [o["id"] for o in body["objects"]] == ["o1", "o2"]
    shoe, card = body["objects"]
    assert shoe["photo_ids"] == [fids[0], fids[2]] and card["photo_ids"] == [fids[1]]
    assert shoe["count"] == 1
    assert card["kind"] == "proof" and card["proof_for"] == "o1" and card["proof_type_label"]
    assert card["missing"] == [] and card["compositions"] == []
    assert [p["object"] for p in body["photos"]] == ["o1", "o2", "o1"]
    assert body["photos"][1]["view"] is None                 # 근거 사진엔 각도 없음
    assert body["main_object"] == "o1"
    assert body["user_edited"] is False and body["needs_review"] is False
    # top-level 은 대표 물건(운동화) 기준
    assert body["item"] == "sneakers" and body["category"] == "shoes"
    assert [m["view"] for m in body["missing"]] == ["back", "bottom"] == [m["view"] for m in shoe["missing"]]
    assert body["compositions"] == shoe["compositions"]
    saved = _saved(isolated_storage, body["item_id"])
    assert len(saved["objects"]) == 2 and all("unclassified" not in p for p in saved["photos"])


def test_main_object_is_for_sale_product_with_most_photos(client, views):
    raw = {"objects": [_o("A", name="mug", category="other"), _o("B", name="bag", category="bag"),
                       _o("C", name="table", category="other", for_sale=False)],
           "photos": [_p(0, "A"), _p(1, "B"), _p(2, "B"), _p(3, "C"), _p(4, "C"), _p(5, "C")]}
    body = _create_raw(client, views, raw, 6)
    assert body["main_object"] == "o2" and body["item"] == "bag" and body["category"] == "bag"
    assert [m["view"] for m in body["missing"]] == ["front", "back", "bottom", "inside"]


def test_only_proof_photos_have_no_main_object(client, views):
    raw = {"objects": [_o("A", "proof", proof_type="screen")], "photos": [_p(0, "A"), _p(1, "A")]}
    body = _create_raw(client, views, raw, 2)
    assert body["main_object"] is None
    assert body["item"] == "object" and body["category"] == "other"
    assert body["missing"] == [] and body["compositions"] == [] and body["complete"] is False


def test_top_level_retake_only_for_sale_product_photos(client, views):
    raw = {"objects": [_o("A"), _o("B", "proof"), _o("C", for_sale=False)],
           "photos": [_p(0, "A", "front", blurry=True), _p(1, "B", None, blurry=True, occluded=True),
                      _p(2, None, None, blurry=True), _p(3, "A", "back"), _p(4, "C", "front", occluded=True)]}
    body = _create_raw(client, views, raw, 5)
    fids = [p["file_id"] for p in body["photos"]]
    assert body["retake"] == [{"file_id": fids[0], "reason": "흐려요"}]
    assert body["objects"][1]["retake"] == []
    assert body["objects"][2]["retake"] == [{"file_id": fids[4], "reason": "손이나 다른 물건에 가려진 부분이 있어요"}]
    assert body["complete"] is False


def test_top_level_retake_includes_non_main_for_sale_product(client, views):
    """대표 물건이 아닌 파는 상품 사진이 흐려도 top-level retake 에 나오고 complete 가 아니다."""
    raw = {"objects": [_o("A"), _o("B")],
           "photos": [_p(0, "A", "front"), _p(1, "A", "back"), _p(2, "B", "front", blurry=True)]}
    body = _create_raw(client, views, raw, 3)
    assert body["main_object"] == "o1" and body["missing"] == []
    assert body["retake"] == [{"file_id": body["photos"][2]["file_id"], "reason": "흐려요"}]
    assert body["complete"] is False


def test_not_for_sale_retake_does_not_block_complete(client, views):
    raw = {"objects": [_o("A"), _o("B", for_sale=False)],
           "photos": [_p(0, "A", "front"), _p(1, "A", "back"), _p(2, "B", "front", blurry=True)]}
    body = _create_raw(client, views, raw, 3)
    assert body["retake"] == [] and body["complete"] is True


def test_create_item_classify_failure_marks_unclassified(client, views, isolated_storage):
    views["fail"] = True
    body = client.post("/api/items", files=_files(("a.png", _png()))).json()
    assert body["photos"][0]["object"] is None
    assert body["views_failed"] is True and body["missing"] == [] and body["complete"] is False
    saved = _saved(isolated_storage, body["item_id"])
    assert saved["photos"][0]["unclassified"] is True
    assert "unclassified" not in body["photos"][0]             # 응답 스키마엔 없다


def test_create_item_classify_failure_has_no_phantom_object(client, views):
    views["fail"] = True
    body = client.post("/api/items", files=_files(("a.png", _png()))).json()
    assert body["objects"] == [] and body["main_object"] is None


# ── PUT /api/items/{item_id}/objects ──
def test_put_objects_renames_moves_and_saves(client, views, isolated_storage):
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    fids = [p["file_id"] for p in body["photos"]]
    edit = _edit_body(body)
    edit["objects"][0].update(label="나이키 운동화", desc="밑창 약간 닳음", count=2)
    edit["objects"].append({"id": "o3", "kind": "product", "label": "신발 상자", "desc": "", "category": "other",
                            "for_sale": False})
    edit["photos"][2].update(object="o3", view="top")
    r = _put(client, body, edit)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["user_edited"] is True and got["needs_review"] is False
    assert [o["id"] for o in got["objects"]] == ["o1", "o2", "o3"]
    o1, _, o3 = got["objects"]
    assert o1["label"] == "나이키 운동화" and o1["name"] == "sneakers" and o1["count"] == 2
    assert o1["photo_ids"] == [fids[0]] and o3["photo_ids"] == [fids[2]]
    assert o3["name"] == "item" and o3["label"] == "신발 상자"     # 사용자 label 은 name 에 안 들어간다
    assert [p["object"] for p in got["photos"]] == ["o1", "o2", "o3"]
    assert got["photos"][2]["view"] == "top" and got["photos"][2]["view_label"] == "위에서"
    assert got["main_object"] == "o1"                        # 신발 상자는 팔지 않음
    assert client.get(f"/api/items/{body['item_id']}").json() == got
    saved = _saved(isolated_storage, body["item_id"])
    assert saved["user_edited"] is True and saved["needs_review"] is False and len(saved["objects"]) == 3


def test_put_objects_category_change_renames_and_top_level(client, views):
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    edit = _edit_body(body)
    edit["objects"][0]["category"] = "bag"
    got = _put(client, body, edit).json()
    assert got["objects"][0]["name"] == "bag" and got["item"] == "bag" and got["category"] == "bag"


def test_put_objects_unsale_main_switches_main(client, views):
    body = _create_raw(client, views, {"objects": [_o("A", name="mug"), _o("B", name="plate")],
                                       "photos": [_p(0, "A"), _p(1, "A"), _p(2, "B")]}, 3)
    assert body["main_object"] == "o1"
    edit = _edit_body(body)
    edit["objects"][0]["for_sale"] = False
    got = _put(client, body, edit).json()
    assert got["main_object"] == "o2" and got["item"] == "plate"


def test_put_objects_removes_object_without_photos(client, views):
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    edit = _edit_body(body)
    edit["photos"][1].update(object="o1", view="detail")    # 보증서 사진을 운동화로 옮김
    got = _put(client, body, edit).json()
    assert [o["id"] for o in got["objects"]] == ["o1"]
    assert got["photos"][1]["view"] == "detail"


def test_put_objects_minimal_body_defaults(client, views):
    """label · for_sale 등을 빼면 스키마 기본값 — for_sale 기본 False, label 비면 name."""
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    edit = {"objects": [{"id": "o1", "kind": "product", "category": "shoes"}],
            "photos": [{"file_id": p["file_id"], "object": "o1"} for p in body["photos"]]}
    got = _put(client, body, edit)
    assert got.status_code == 200, got.text
    o = got.json()["objects"][0]
    assert o["for_sale"] is False and o["label"] == "sneakers" and o["name"] == "sneakers" and o["count"] == 1
    assert all(p["view"] is None for p in got.json()["photos"])
    assert got.json()["main_object"] == "o1"                # 파는 게 없으면 상품 중에서


@pytest.mark.parametrize("change,needle", [
    (lambda e: e["photos"].pop(), "사진이 빠졌다"),
    (lambda e: e["photos"][0].update(object="o9"), "없는 물건"),
    (lambda e: e["photos"][0].update(file_id=UNKNOWN), "이 묶음에 없거나"),
    (lambda e: e["photos"].append(dict(e["photos"][0])), "두 번"),
    (lambda e: e["photos"][0].update(view="diagonal"), "view"),
    (lambda e: e["objects"][0].update(category="toy"), "category"),
    (lambda e: e["objects"][1].update(proof_type="receipt"), "proof_type"),
    (lambda e: e["objects"][1].update(id="o1"), "물건 id"),
])
def test_put_objects_400_keeps_item(client, views, isolated_storage, change, needle):
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    before = (isolated_storage / "items" / f"{body['item_id']}.json").read_bytes()
    edit = _edit_body(body)
    change(edit)
    r = _put(client, body, edit)
    assert r.status_code == 400 and needle in r.json()["detail"], r.text
    assert (isolated_storage / "items" / f"{body['item_id']}.json").read_bytes() == before


def test_put_objects_400_detail_capped_at_five_errors(client, views):
    body = _create_raw(client, views, {"objects": [_o("A")], "photos": [_p(i, "A") for i in range(8)]}, 8)
    edit = _edit_body(body)
    for ph in edit["photos"]:
        ph["object"] = "o9"
    r = _put(client, body, edit)
    assert r.status_code == 400 and r.json()["detail"].count("없는 물건") == 5


def test_put_objects_unknown_item_404(client, views):
    r = client.put(f"/api/items/{UNKNOWN}/objects", json={"objects": [], "photos": []})
    assert r.status_code == 404


@pytest.mark.parametrize("bad", ["xyz", "0123456789ABCDEF0123456789ABCDEF", UNKNOWN + "0", "g" * 32])
def test_put_objects_bad_item_id_422(client, views, bad):
    assert client.put(f"/api/items/{bad}/objects", json={"objects": [], "photos": []}).status_code == 422


_OK_OBJ = {"id": "o1", "kind": "product", "category": "other"}


@pytest.mark.parametrize("payload", [
    {"objects": [{"id": "o1", "kind": "thing"}], "photos": []},          # kind 는 Literal
    {"objects": [{"kind": "product"}], "photos": []},                     # id 없음
    {"objects": [{**_OK_OBJ, "id": "o12345678"}], "photos": []},          # id 8자 넘음
    {"objects": [{**_OK_OBJ, "id": "x1"}], "photos": []},                 # id 패턴 (o 뒤에 숫자)
    {"objects": [{**_OK_OBJ, "id": "o1\n"}], "photos": []},               # 끝 개행
    {"objects": [], "photos": [{"file_id": UNKNOWN, "object": "x1"}]},
    {"objects": [{**_OK_OBJ, "label": "가" * 41}], "photos": []},
    {"objects": [{**_OK_OBJ, "desc": "가" * 201}], "photos": []},
    {"objects": [{**_OK_OBJ, "category": "c" * 21}], "photos": []},
    {"objects": [{**_OK_OBJ, "count": 0}], "photos": []},
    {"objects": [{**_OK_OBJ, "count": 100}], "photos": []},
    {"objects": [{**_OK_OBJ, "proof_for": "o123456789"}], "photos": []},
    {"objects": [{**_OK_OBJ, "id": f"o{i}"} for i in range(21)], "photos": []},     # 물건 20개 넘음
    {"objects": [], "photos": [{"file_id": UNKNOWN}] * 61},                         # 사진 60장 넘음
    {"objects": [], "photos": [{"file_id": "nope"}]},                               # file_id 패턴
    {"objects": [], "photos": [{"file_id": UNKNOWN.upper()}]},
    {"objects": [], "photos": [{"file_id": UNKNOWN, "object": "o123456789"}]},
    {"objects": [], "photos": [{"file_id": UNKNOWN, "view": "v" * 21}]},
    {"objects": [], "photos": [{"object": "o1"}]},                                  # file_id 없음
    {"objects": []},                                                                # photos 없음
    {"photos": []},                                                                 # objects 없음
    {"objects": "o1", "photos": []},
])
def test_put_objects_schema_422(client, views, isolated_storage, payload):
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    before = (isolated_storage / "items" / f"{body['item_id']}.json").read_bytes()
    assert _put(client, body, payload).status_code == 422
    assert (isolated_storage / "items" / f"{body['item_id']}.json").read_bytes() == before


@pytest.mark.parametrize("count", [1, 99])
def test_put_objects_count_bounds_ok(client, views, count):
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    edit = _edit_body(body)
    edit["objects"][0]["count"] = count
    r = _put(client, body, edit)
    assert r.status_code == 200 and r.json()["objects"][0]["count"] == count


def test_put_objects_does_not_call_vlm(client, views):
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    n = len(views["calls"])
    _put(client, body, _edit_body(body))
    assert len(views["calls"]) == n


# ── 고친 뒤 더 올리기 ──
def test_add_files_after_edit_keeps_user_grouping(client, views):
    """AI 는 사진 0·1·2 를 같은 물건으로 봤지만 사용자가 나눴다 → 더 올려도 나눈 대로, 새 사진은 다수결."""
    body = _create_raw(client, views, {"objects": [_o("A", name="mug")],
                                       "photos": [_p(0, "A", "front"), _p(1, "A", "back"), _p(2, "A", "side")]}, 3)
    edit = _edit_body(body)
    edit["objects"][0]["label"] = "내 컵"
    edit["objects"].append({"id": "o2", "kind": "product", "label": "다른 컵", "category": "other",
                            "for_sale": True})
    edit["photos"][2]["object"] = "o2"
    assert _put(client, body, edit).status_code == 200
    views["raw"] = {"objects": [_o("A", name="mug", label="AI 컵")],
                    "photos": [_p(i, "A", v) for i, v in enumerate(["front", "back", "side", "top"])]}
    got = client.post(f"/api/items/{body['item_id']}/files", files=_files(("d.png", _png()))).json()
    assert got["user_edited"] is True and got["needs_review"] is True
    assert [o["label"] for o in got["objects"]] == ["내 컵", "다른 컵"]
    assert [p["object"] for p in got["photos"]] == ["o1", "o1", "o2", "o1"]   # 다수결: AI A → 내 o1 (2표)
    assert got["photos"][3]["view"] == "top"
    # 다시 확인(PUT)하면 needs_review 가 꺼진다
    again = _put(client, got, _edit_body(got)).json()
    assert again["needs_review"] is False


def test_add_files_after_edit_adds_new_ai_object(client, views):
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    _put(client, body, _edit_body(body))
    views["raw"] = {"objects": [*SHOES_AND_CARD["objects"], _o("C", name="cap", label="모자", category="clothing")],
                    "photos": [*SHOES_AND_CARD["photos"], _p(3, "C", "front"), _p(4, "C", "back")]}
    got = client.post(f"/api/items/{body['item_id']}/files",
                      files=_files(("d.png", _png()), ("e.png", _png()))).json()
    assert [o["id"] for o in got["objects"]] == ["o1", "o2", "o3"]
    assert got["objects"][2]["label"] == "모자" and got["objects"][2]["category"] == "clothing"
    assert [p["object"] for p in got["photos"]] == ["o1", "o2", "o1", "o3", "o3"]
    assert got["objects"][1]["proof_for"] == "o1"


def test_add_files_not_edited_regroups_everything(client, views):
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    views["raw"] = {"objects": [_o("X", name="boots", category="shoes")],
                    "photos": [_p(i, "X", "side") for i in range(4)]}
    got = client.post(f"/api/items/{body['item_id']}/files", files=_files(("d.png", _png()))).json()
    assert [o["name"] for o in got["objects"]] == ["boots"]
    assert [p["object"] for p in got["photos"]] == ["o1"] * 4
    assert got["user_edited"] is False and got["needs_review"] is False


def test_add_files_after_edit_failure_keeps_grouping(client, views, isolated_storage):
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    _put(client, body, _edit_body(body))
    views["fail"] = True
    got = client.post(f"/api/items/{body['item_id']}/files", files=_files(("d.png", _png()))).json()
    assert got["views_failed"] is True and got["user_edited"] is True
    assert [p["object"] for p in got["photos"]] == ["o1", "o2", "o1", None]
    assert [o["id"] for o in got["objects"]] == ["o1", "o2"]
    saved = _saved(isolated_storage, body["item_id"])
    assert [p.get("unclassified", False) for p in saved["photos"]] == [False, False, False, True]


def test_add_files_after_edit_recovers_photo_from_failed_upload(client, views, isolated_storage):
    """고친 묶음에 묶기 실패로 '모름'이 된 사진은 다음 업로드 때 새 사진처럼 다시 붙는다."""
    body = _create_raw(client, views, {"objects": [_o("A")], "photos": [_p(0, "A", "front")]}, 1)
    _put(client, body, _edit_body(body))
    views["fail"] = True
    client.post(f"/api/items/{body['item_id']}/files", files=_files(("b.png", _png())))
    views["fail"] = False
    views["raw"] = {"objects": [_o("A")], "photos": [_p(i, "A", v) for i, v in enumerate(["front", "back", "side"])]}
    got = client.post(f"/api/items/{body['item_id']}/files", files=_files(("c.png", _png()))).json()
    assert [p["object"] for p in got["photos"]] == ["o1", "o1", "o1"]
    assert got["photos"][1]["view"] == "back" and got["views_failed"] is False and got["needs_review"] is True
    assert all("unclassified" not in p for p in _saved(isolated_storage, body["item_id"])["photos"])


def test_add_files_lost_original_photo_keeps_its_object(client, views, isolated_storage):
    """원본이 사라진 사진은 다시 분류하지 않는다 — 고친 묶음이면 그 사진의 물건도 그대로."""
    body = _create_raw(client, views, SHOES_AND_CARD, 3)
    _put(client, body, _edit_body(body))
    (isolated_storage / "original" / f"{body['photos'][1]['file_id']}.jpg").unlink()
    views["raw"] = {"objects": [_o("A", category="shoes")], "photos": [_p(i, "A", "back") for i in range(3)]}
    got = client.post(f"/api/items/{body['item_id']}/files", files=_files(("d.png", _png()))).json()
    assert len(views["calls"][-1]) == 3
    assert [p["object"] for p in got["photos"]] == ["o1", "o2", "o1", "o1"]


def test_add_files_lost_original_pointing_to_vanished_object_cleared(client, views, isolated_storage):
    """고치지 않은 묶음을 다시 묶었더니 원본 없는 사진이 가리키던 물건이 사라졌다 → 그 사진은 object None."""
    body = _create_raw(client, views, SHOES_AND_CARD, 3)        # 사진 1 → o2 (보증서)
    (isolated_storage / "original" / f"{body['photos'][1]['file_id']}.jpg").unlink()
    views["raw"] = {"objects": [_o("A", category="shoes")], "photos": [_p(i, "A", "side") for i in range(3)]}
    got = client.post(f"/api/items/{body['item_id']}/files", files=_files(("d.png", _png()))).json()
    assert [o["id"] for o in got["objects"]] == ["o1"]
    assert [p["object"] for p in got["photos"]] == ["o1", None, "o1", "o1"]


# ── 옛 형식 묶음 (objects 없음, 10-04 이전) ──
def _make_old_item(client, views, isolated_storage, n=2):
    views["photos"] = [_v("front"), _v("back", blurry=True)][:n]
    views["category"], views["item"] = "bag", "tote"
    body = _create(client, n)
    saved = _saved(isolated_storage, body["item_id"])
    for k in ("objects", "user_edited", "needs_review"):
        saved.pop(k, None)
    saved.update(category="bag", item="tote")
    for p in saved["photos"]:
        p.pop("object", None)
    (isolated_storage / "items" / f"{body['item_id']}.json").write_text(json.dumps(saved))
    return body


def test_get_old_format_item_becomes_one_object(client, views, isolated_storage):
    body = _make_old_item(client, views, isolated_storage)
    before = (isolated_storage / "items" / f"{body['item_id']}.json").read_bytes()
    r = client.get(f"/api/items/{body['item_id']}")
    assert r.status_code == 200, r.text
    got = r.json()
    assert [(o["id"], o["name"], o["category"]) for o in got["objects"]] == [("o1", "tote", "bag")]
    assert got["main_object"] == "o1" and got["user_edited"] is False and got["needs_review"] is False
    assert got["item"] == "tote" and got["category"] == "bag"
    assert [p["object"] for p in got["photos"]] == ["o1", "o1"]
    assert [p["view"] for p in got["photos"]] == ["front", "back"]
    assert [m["view"] for m in got["missing"]] == ["back", "bottom", "inside"]     # 흐린 뒷면은 안 친다
    assert got["retake"] == [{"file_id": got["photos"][1]["file_id"], "reason": "흐려요"}]
    assert (isolated_storage / "items" / f"{body['item_id']}.json").read_bytes() == before   # GET 은 저장 안 함


def test_put_objects_on_old_format_item(client, views, isolated_storage):
    body = _make_old_item(client, views, isolated_storage)
    edit = {"objects": [{"id": "o1", "kind": "product", "label": "가방", "category": "bag", "for_sale": True}],
            "photos": [{"file_id": p["file_id"], "object": "o1", "view": p["view"]} for p in body["photos"]]}
    r = _put(client, body, edit)
    assert r.status_code == 200, r.text
    got = r.json()
    assert got["main_object"] == "o1" and got["category"] == "bag" and got["user_edited"] is True
    assert got["objects"][0]["name"] == "tote" and got["objects"][0]["label"] == "가방"   # 옛 item 이름 유지
    assert len(_saved(isolated_storage, body["item_id"])["objects"]) == 1


def test_add_files_to_old_format_item_groups(client, views, isolated_storage):
    body = _make_old_item(client, views, isolated_storage)
    views["raw"] = {"objects": [_o("A", name="tote", category="bag")],
                    "photos": [_p(0, "A", "front"), _p(1, "A", "back"), _p(2, "A", "bottom")]}
    got = client.post(f"/api/items/{body['item_id']}/files", files=_files(("c.png", _png()))).json()
    assert [p["object"] for p in got["photos"]] == ["o1"] * 3 and got["main_object"] == "o1"
    assert [m["view"] for m in got["missing"]] == ["inside"]


# ── POST /api/items/{item_id}/arrange (10-04) ──
TWO_SHOES_AND_CARD = {
    "objects": [_o("A", name="sneakers", category="shoes"), _o("B", name="bag", category="bag"),
                _o("C", "proof", proof_for="A"), _o("D", name="table", for_sale=False)],
    "photos": [_p(0, "A", "bottom"), _p(1, "A", "front_34"), _p(2, "B", "front"), _p(3, "B", "back"),
               _p(4, "C"), _p(5, "D", "front")],
}


@pytest.fixture()
def arrange_rec(monkeypatch):
    """compositor.arrange · pipeline.load_analysis 가짜. rec["analysis"]: file_id → 분석 dict, rec["error"]: ValueError."""
    from app.services import pipeline
    from app.services.ai import compositor
    rec = {"calls": [], "analysis": {}, "error": None, "analysis_calls": []}

    def fake_arrange(parts, bg_color, layout="row"):
        rec["calls"].append({"parts": parts, "bg": bg_color, "layout": layout})
        if rec["error"]:
            raise ValueError(rec["error"])
        return _jpeg((1, 2, 3))

    def fake_load(fid):
        rec["analysis_calls"].append(fid)
        return rec["analysis"].get(fid)
    monkeypatch.setattr(compositor, "arrange", fake_arrange)
    monkeypatch.setattr(pipeline, "load_analysis", fake_load)
    return rec


def _arr_item(client, views):
    return _create_raw(client, views, TWO_SHOES_AND_CARD, 6)


def _arrange(client, body, payload):
    return client.post(f"/api/items/{body['item_id']}/arrange", json=payload)


def _results(root):
    return sorted(p.name for p in (root / "result").iterdir())


def test_arrange_success_saves_result(client, views, arrange_rec, isolated_storage):
    from app.prompts.presets import PRESETS
    body = _arr_item(client, views)
    fids = [p["file_id"] for p in body["photos"]]
    n_calls = len(views["calls"])
    r = _arrange(client, body, {"objects": ["o2", "o1"], "layout": "grid"})
    assert r.status_code == 200, r.text
    out = r.json()
    # 물건 순서대로 — 가방은 구도 후보가 없어 문제 없는 첫 사진, 운동화는 대표컷(front_34) 구도 사진
    assert out["photo_ids"] == [fids[2], fids[1]]
    name = out["result_url"].rsplit("/", 1)[1]
    assert out["result_url"] == f"/storage/result/{name}"
    assert name.startswith(f"{body['item_id']}_arrange_grid_") and name.endswith(".jpg")
    assert _results(isolated_storage) == [name]
    with Image.open(isolated_storage / "result" / name) as img:       # 가짜 arrange 의 48x48 JPEG
        assert img.format == "JPEG" and img.size == (48, 48)
    call = arrange_rec["calls"][0]
    assert call["layout"] == "grid" and call["bg"] == PRESETS["studio_white"]["bg_color"]
    stored = isolated_storage / "original"
    assert [p["image"] for p in call["parts"]] == [(stored / f"{f}.jpg").read_bytes() for f in out["photo_ids"]]
    # 미리 분석이 없으면 박스 없음. 가방(하나)은 single, 운동화(켤레)는 아님
    assert [{k: v for k, v in p.items() if k != "image"} for p in call["parts"]] == [
        {"box": None, "drop": [], "keep": [], "single": True},
        {"box": None, "drop": [], "keep": [], "single": False}]
    assert len(views["calls"]) == n_calls                         # VLM 을 부르지 않는다
    assert client.get(out["result_url"]).status_code == 200


def test_arrange_default_layout_row(client, views, arrange_rec):
    body = _arr_item(client, views)
    r = _arrange(client, body, {"objects": ["o1", "o2"]})
    assert r.status_code == 200 and "_arrange_row_" in r.json()["result_url"]
    assert arrange_rec["calls"][0]["layout"] == "row"


def test_arrange_uses_saved_analysis_boxes(client, views, arrange_rec):
    body = _arr_item(client, views)
    fids = [p["file_id"] for p in body["photos"]]
    box = {"x1": 100, "y1": 120, "x2": 800, "y2": 900}
    sold, other = {"x1": 0, "y1": 0, "x2": 500, "y2": 500}, {"x1": 500, "y1": 0, "x2": 1000, "y2": 500}
    arrange_rec["analysis"][fids[1]] = {"item_box": box, "objects": [
        {"box": sold, "for_sale": True}, {"box": other, "for_sale": False}, {"box": other},   # for_sale 없음 = 지움
        {"for_sale": False}, "junk", {"box": None, "for_sale": False}]}                       # 박스 없는 건 무시
    _arrange(client, body, {"objects": ["o1", "o2"]})
    p1, p2 = arrange_rec["calls"][0]["parts"]
    assert p1["box"] == box and p1["keep"] == [sold] and p1["drop"] == [other, other]
    assert p2["box"] is None and p2["drop"] == [] and p2["keep"] == []
    assert arrange_rec["analysis_calls"] == [fids[1], fids[2]]


def test_arrange_single_follows_count_and_category(client, views, arrange_rec):
    """물건 하나(count 1, 신발 아님)만 single — 여러 개 · 신발(켤레)은 덩어리를 다 둔다."""
    body = _arr_item(client, views)
    edit = _edit_body(body)
    edit["objects"][1]["count"] = 2
    assert _put(client, body, edit).status_code == 200
    _arrange(client, body, {"objects": ["o1", "o2"]})
    assert [p["single"] for p in arrange_rec["calls"][0]["parts"]] == [False, False]


def test_arrange_busy_429(client, views, arrange_rec, monkeypatch, isolated_storage):
    from app.api.routes import items
    import threading
    monkeypatch.setattr(items, "_ARRANGE_SLOTS", threading.BoundedSemaphore(1))
    body = _arr_item(client, views)
    items._ARRANGE_SLOTS.acquire()
    try:
        r = _arrange(client, body, {"objects": ["o1", "o2"]})
        assert r.status_code == 429 and "다른 배치" in r.json()["detail"]
        assert arrange_rec["calls"] == [] and _results(isolated_storage) == []
    finally:
        items._ARRANGE_SLOTS.release()
    assert _arrange(client, body, {"objects": ["o1", "o2"]}).status_code == 200


def test_arrange_slot_released_after_error(client, views, arrange_rec, monkeypatch):
    from app.api.routes import items
    import threading
    monkeypatch.setattr(items, "_ARRANGE_SLOTS", threading.BoundedSemaphore(1))
    body = _arr_item(client, views)
    arrange_rec["error"] = "빈 알파"
    assert _arrange(client, body, {"objects": ["o1", "o2"]}).status_code == 422
    arrange_rec["error"] = None
    assert _arrange(client, body, {"objects": ["o1", "o2"]}).status_code == 200


def test_arrange_photos_override(client, views, arrange_rec):
    body = _arr_item(client, views)
    fids = [p["file_id"] for p in body["photos"]]
    r = _arrange(client, body, {"objects": ["o1", "o2"], "photos": {"o1": fids[0], "o2": fids[3]}})
    assert r.status_code == 200 and r.json()["photo_ids"] == [fids[0], fids[3]]


def test_arrange_photos_for_other_objects_ignored(client, views, arrange_rec):
    body = _arr_item(client, views)
    fids = [p["file_id"] for p in body["photos"]]
    r = _arrange(client, body, {"objects": ["o1", "o2"], "photos": {"o3": fids[4]}})
    assert r.status_code == 200 and r.json()["photo_ids"] == [fids[1], fids[2]]


@pytest.mark.parametrize("payload_fn,needle", [
    (lambda f: {"objects": ["o1", "o1"]}, "두 번"),
    (lambda f: {"objects": ["o1", "o3"]}, "파는 물건이 아니에요: o3"),          # 근거 사진
    (lambda f: {"objects": ["o1", "o4"]}, "파는 물건이 아니에요: o4"),          # for_sale False
    (lambda f: {"objects": ["o1", "o9"]}, "파는 물건이 아니에요: o9"),          # 없는 물건
    (lambda f: {"objects": ["o1", "o2"], "photos": {"o1": f[2]}}, "사진이 아니에요"),   # 가방 사진
    (lambda f: {"objects": ["o1", "o2"], "photos": {"o2": UNKNOWN}}, "사진이 아니에요"),
])
def test_arrange_400(client, views, arrange_rec, isolated_storage, payload_fn, needle):
    body = _arr_item(client, views)
    fids = [p["file_id"] for p in body["photos"]]
    r = _arrange(client, body, payload_fn(fids))
    assert r.status_code == 400 and needle in r.json()["detail"], r.text
    assert arrange_rec["calls"] == [] and _results(isolated_storage) == []


def test_arrange_after_edit_unsale_is_400(client, views, arrange_rec):
    body = _arr_item(client, views)
    edit = _edit_body(body)
    edit["objects"][1]["for_sale"] = False
    assert _put(client, body, edit).status_code == 200
    assert _arrange(client, body, {"objects": ["o1", "o2"]}).status_code == 400


def test_arrange_unknown_item_404(client, views, arrange_rec):
    r = client.post(f"/api/items/{UNKNOWN}/arrange", json={"objects": ["o1", "o2"]})
    assert r.status_code == 404


def test_arrange_missing_original_404(client, views, arrange_rec, isolated_storage):
    body = _arr_item(client, views)
    (isolated_storage / "original" / f"{body['photos'][2]['file_id']}.jpg").unlink()
    r = _arrange(client, body, {"objects": ["o1", "o2"]})
    assert r.status_code == 404 and "사진을 찾을 수 없어요" in r.json()["detail"]
    assert arrange_rec["calls"] == [] and _results(isolated_storage) == []


def test_arrange_cutout_failure_422(client, views, arrange_rec, isolated_storage):
    arrange_rec["error"] = "물건을 찾지 못함 (알파가 비어 있음)"
    body = _arr_item(client, views)
    r = _arrange(client, body, {"objects": ["o1", "o2"]})
    assert r.status_code == 422 and "물건을 오리지 못했어요" in r.json()["detail"]
    assert "알파가 비어" in r.json()["detail"]
    assert _results(isolated_storage) == []


@pytest.mark.parametrize("payload", [
    {"objects": ["o1"]},                                          # 2개 미만
    {"objects": [f"o{i}" for i in range(1, 8)]},                 # 6개 초과
    {"objects": []},
    {"objects": ["o1", "o2"], "layout": "circle"},
    {"objects": ["o1", "o2"], "photos": ["x"]},
    {"objects": "o1,o2"},
    {},
    {"objects": ["o1", "A"]},                                    # id 패턴
    {"objects": ["o1", "o1\n"]},
    {"objects": ["o1", "o2"], "photos": {"o1": ""}},             # file_id 패턴
    {"objects": ["o1", "o2"], "photos": {"o1": "nope"}},
    {"objects": ["o1", "o2"], "photos": {"x": UNKNOWN}},
    {"objects": ["o1", "o2"], "photos": {f"o{i}": UNKNOWN for i in range(7)}},   # 6개 넘음
])
def test_arrange_schema_422(client, views, arrange_rec, payload):
    body = _arr_item(client, views)
    assert _arrange(client, body, payload).status_code == 422
    assert arrange_rec["calls"] == []


@pytest.mark.parametrize("bad", ["xyz", "0123456789ABCDEF0123456789ABCDEF", UNKNOWN + "0"])
def test_arrange_bad_item_id_422(client, views, arrange_rec, bad):
    assert client.post(f"/api/items/{bad}/arrange", json={"objects": ["o1", "o2"]}).status_code == 422


def test_arrange_old_format_item_has_one_object(client, views, arrange_rec, isolated_storage):
    body = _make_old_item(client, views, isolated_storage)
    r = _arrange(client, body, {"objects": ["o1", "o2"]})
    assert r.status_code == 400 and "o2" in r.json()["detail"]


def test_arrange_real_compositor_end_to_end(client, views, monkeypatch, isolated_storage):
    """compositor.arrange 는 진짜로, 오리기(original_alpha)만 가짜 — 정사각 JPEG 가 저장된다."""
    import numpy as np
    from app.services import pipeline
    from app.services.ai import compositor
    monkeypatch.setattr(pipeline, "load_analysis", lambda fid: None)

    def fake_alpha(image_bytes, img):
        a = np.zeros((img.height, img.width), np.uint8)
        a[img.height // 4: img.height * 3 // 4, img.width // 4: img.width * 3 // 4] = 255
        return a
    monkeypatch.setattr(compositor, "original_alpha", fake_alpha)
    body = _arr_item(client, views)
    r = _arrange(client, body, {"objects": ["o1", "o2"], "layout": "overlap"})
    assert r.status_code == 200, r.text
    name = r.json()["result_url"].rsplit("/", 1)[1]
    with Image.open(isolated_storage / "result" / name) as img:
        assert img.format == "JPEG" and img.size == (compositor.CANVAS, compositor.CANVAS)
