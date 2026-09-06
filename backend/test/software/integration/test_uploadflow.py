# test_uploadflow.py
def test_upload_saves_original(client, tmp_storage, make_png):
    r = client.post("/api/images/upload",
                    files={"file": ("hoodie.png", make_png(), "image/png")})
    assert r.status_code == 200
    fid = r.json()["file_id"]

    # ⭐ 확장자는 무엇이든 OK, "있는지"만 확인
    found = list((tmp_storage / "original").glob(f"{fid}.*"))
    assert len(found) == 1, f"파일 1개 기대, {len(found)}개: {found}"