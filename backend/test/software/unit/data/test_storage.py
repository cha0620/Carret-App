from PIL import Image

from app.services import storage


def _force_local(monkeypatch):
    """.env 의 STORAGE_BACKEND=s3 와 무관하게, storage.BACKEND 모듈 전역을
    강제로 LocalBackend 로 바꿔치기한다. BACKEND 는 모듈 임포트 시점에
    한 번만 만들어지는 전역이라 settings.storage_backend 만 바꿔서는
    이미 만들어진 인스턴스에 영향을 주지 못한다."""
    monkeypatch.setattr(storage, "BACKEND", storage.LocalBackend())


def test_original_of_missing(tmp_storage):
    assert storage.original_of("ghost") is None


def test_save_caps_big_image(tmp_storage, make_png):
    storage.save("result", "t.jpg", make_png(3000, 2000))
    with Image.open(tmp_storage / "result" / "t.jpg") as im:
        assert max(im.size) <= 1600


def test_save_normalizes_original_kind(tmp_storage, make_png, monkeypatch):
    """kind='original' 도 정규화 대상(IMAGE_KINDS)이므로 리사이즈 + JPEG 변환."""
    _force_local(monkeypatch)
    storage.save("original", "big.jpg", make_png(3000, 2000))
    with Image.open(tmp_storage / "original" / "big.jpg") as im:
        assert max(im.size) <= 1600
        assert im.format == "JPEG"


def test_save_does_not_normalize_non_image_kind(tmp_storage, monkeypatch):
    """IMAGE_KINDS 에 없는 kind(e.g. 'quality')는 img_util.normalize 를
    타지 않으므로, 이미지가 아닌 바이트를 넣어도 그대로 저장돼야 한다."""
    _force_local(monkeypatch)
    raw = b"not an image, just some bytes"
    storage.save("quality", "meta.json", raw)
    assert (tmp_storage / "quality" / "meta.json").read_bytes() == raw


def test_exists_reflects_saved_state(tmp_storage, monkeypatch):
    _force_local(monkeypatch)
    assert storage.exists("quality", "ghost.json") is False
    storage.save("quality", "ghost.json", b"{}")
    assert storage.exists("quality", "ghost.json") is True


def test_load_original_finds_non_default_extension(tmp_storage, monkeypatch):
    """load_original 은 .jpg/.jpeg/.png/.webp 순서로 탐색한다."""
    _force_local(monkeypatch)
    fid = "extonly"
    (tmp_storage / "original" / f"{fid}.webp").write_bytes(b"webp-bytes")
    assert storage.load_original(fid) == b"webp-bytes"


def test_load_original_prefers_jpg_over_other_extensions(tmp_storage, monkeypatch):
    _force_local(monkeypatch)
    fid = "dupext"
    (tmp_storage / "original" / f"{fid}.png").write_bytes(b"png-bytes")
    (tmp_storage / "original" / f"{fid}.jpg").write_bytes(b"jpg-bytes")
    assert storage.load_original(fid) == b"jpg-bytes"


def test_load_original_missing_returns_none(tmp_storage, monkeypatch):
    _force_local(monkeypatch)
    assert storage.load_original("totally-missing") is None


def test_result_url_format():
    assert (storage.result_url("abc123", "studio_white")
            == "/storage/result/abc123_studio_white.jpg")