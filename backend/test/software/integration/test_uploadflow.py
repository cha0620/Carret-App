# test_uploadflow.py
def test_upload_saves_original(client, tmp_storage, make_png):
    r = client.post("/api/images/upload",
                    files={"file": ("hoodie.png", make_png(), "image/png")})
    assert r.status_code == 200
    fid = r.json()["file_id"]

    # ⭐ 확장자는 무엇이든 OK, "있는지"만 확인
    found = list((tmp_storage / "original").glob(f"{fid}.*"))
    assert len(found) == 1, f"파일 1개 기대, {len(found)}개: {found}"

def test_upload_in_s3_mode_saves_to_backend_not_local(client, tmp_storage, make_png, monkeypatch):
    """회귀: 예전엔 업로드가 로컬 디스크에 직접 써서 S3 모드에서도 storage/original 이 쌓였다."""
    from app.services.persistence import storage
    saved = {}

    class FakeS3:
        def save(self, kind, name, data):
            saved[(kind, name)] = data

    monkeypatch.setattr(storage, "BACKEND", FakeS3())
    r = client.post("/api/images/upload",
                    files={"file": ("hoodie.png", make_png(), "image/png")})
    assert r.status_code == 200
    fid = r.json()["file_id"]
    assert ("original", f"{fid}.jpg") in saved   # 원본 이름은 .jpg 로 통일
    assert not list((tmp_storage / "original").glob(f"{fid}.*"))


def test_upload_broken_image_returns_400(client, tmp_storage):
    """확장자만 이미지인 깨진 파일은 500 이 아니라 400."""
    r = client.post("/api/images/upload",
                    files={"file": ("fake.png", b"not an image", "image/png")})
    assert r.status_code == 400
