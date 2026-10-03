"""POST /api/items · POST /api/items/{item_id}/files · GET /api/items/{item_id} (여러 각도 업로드).

detector.classify_views 와 video.extract_frames 는 가짜로 바꿔치기 — 실제 VLM 호출·동영상 디코딩 없이
라우트가 무엇을 검사·저장·반환하는지만 본다. storage 는 test/conftest.py 의 isolated_storage(tmp).
"""
import io
import json

import pytest
from PIL import Image

from app.core.config import settings
from app.services import video

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
    """classify_views 가짜. rec["photos"] 로 사진별 답을, rec["fail"] 로 예외를 조절.
    rec["calls"] 에는 매 호출의 이미지 목록."""
    rec = {"calls": [], "category": "other", "item": "mug", "photos": None, "fail": False}

    def fake(images):
        rec["calls"].append(list(images))
        if rec["fail"]:
            raise RuntimeError("VLM 장애")
        photos = rec["photos"] or [{"view": None, "occluded": False, "blurry": False,
                                    "item_visible": True}] * len(images)
        return {"category": rec["category"], "item": rec["item"], "photos": photos[:len(images)]}
    monkeypatch.setattr("app.services.ai.detector.classify_views", fake)
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
    fake = detector.classify_views

    def slow(images):
        time.sleep(0.2)                     # 분류 중에 다른 요청이 같은 묶음을 읽도록 틈을 둔다
        return fake(images)
    monkeypatch.setattr(detector, "classify_views", slow)
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


def test_non_shoes_item_has_no_compositions(client, views):
    views["category"] = "clothing"
    views["photos"] = [_v("front")]
    assert client.post("/api/items", files=_files(("a.png", _png()))).json()["compositions"] == []
