def test_upload_saves_original(client, tmp_storage, make_png):
    r = client.post("/api/images/upload",
                    files={"file": ("hoodie.png", make_png(), "image/png")})
    assert r.status_code == 200
    fid = r.json()["file_id"]
    assert (tmp_storage / "original" / f"{fid}.jpg").exists()