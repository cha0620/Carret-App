from PIL import Image

from app.services import storage


def test_original_of_missing(tmp_storage):
    assert storage.original_of("ghost") is None


def test_save_caps_big_image(tmp_storage, make_png):
    storage.save("result", "t.jpg", make_png(3000, 2000))
    with Image.open(tmp_storage / "result" / "t.jpg") as im:
        assert max(im.size) <= 1600