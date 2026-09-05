def test_pages(client):
    assert client.get("/").status_code == 200
    assert client.get("/test.html").status_code == 200


def test_dev_endpoints(client, tmp_storage):
    for p in ("/dev/originals", "/dev/pairs", "/dev/gallery"):
        assert client.get(p).status_code == 200