from app.util import evaluator


def _seed(tmp_storage):
    (tmp_storage / "dataset" / "img" / "a.png").write_bytes(b"i")
    (tmp_storage / "dataset" / "after" / "a.jpg").write_bytes(b"r")  # 확장자 혼합
    (tmp_storage / "dataset" / "img" / "b.png").write_bytes(b"i")    # after 없음


def test_pairs_extension_agnostic(tmp_storage):
    _seed(tmp_storage)
    assert evaluator.find_pairs() == ["a"]


def test_pair_of_paths(tmp_storage):
    _seed(tmp_storage)
    img, after = evaluator.pair_of("a")
    assert img.exists() and after.exists()


def test_pair_of_missing(tmp_storage):
    assert evaluator.pair_of("ghost") is None