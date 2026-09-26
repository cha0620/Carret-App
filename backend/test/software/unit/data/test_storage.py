from PIL import Image

from app.services.persistence import storage


def _force_local(monkeypatch):
    """.env 의 STORAGE_BACKEND=s3 와 무관하게, storage.BACKEND 모듈 전역을
    강제로 LocalBackend 로 바꿔치기한다. BACKEND 는 모듈 임포트 시점에
    한 번만 만들어지는 전역이라 settings.storage_backend 만 바꿔서는
    이미 만들어진 인스턴스에 영향을 주지 못한다."""
    monkeypatch.setattr(storage, "BACKEND", storage.LocalBackend())


def test_original_of_missing(tmp_storage):
    assert storage.original_of("ghost") is None


def test_save_caps_big_image(tmp_storage, make_png, monkeypatch):
    _force_local(monkeypatch)
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

class FakeS3Backend:
    """boto3 를 전혀 건드리지 않는 가짜 S3 백엔드 — (kind, name) → bytes 딕셔너리 기반."""

    def __init__(self, data: dict | None = None):
        self.data = dict(data or {})

    def save(self, kind: str, name: str, data: bytes) -> None:
        self.data[(kind, name)] = data

    def load(self, kind: str, name: str):
        return self.data.get((kind, name))

    def exists(self, kind: str, name: str) -> bool:
        return (kind, name) in self.data

    def delete(self, kind: str, name: str) -> None:
        self.data.pop((kind, name), None)


def _force_fake_s3(monkeypatch, data: dict | None = None) -> FakeS3Backend:
    """storage.BACKEND 를 가짜 S3Backend 로 바꿔치기 (실제 AWS 호출 없음)."""
    fake = FakeS3Backend(data)
    monkeypatch.setattr(storage, "BACKEND", fake)
    return fake


def _spy_local_load_calls(monkeypatch) -> list:
    """storage._LOCAL.load 호출 여부/횟수를 기록하는 스파이를 심는다."""
    calls = []
    original = storage._LOCAL.load

    def _spy(kind, name):
        calls.append((kind, name))
        return original(kind, name)

    monkeypatch.setattr(storage._LOCAL, "load", _spy)
    return calls


# ===== load() 폴백 시나리오 =====

def test_load_prefers_configured_backend_and_skips_local(tmp_storage, monkeypatch):
    """설정된 백엔드(가짜 S3)에 값이 있으면 그것을 반환하고, 로컬은 조회조차 하지 않는다."""
    fake = _force_fake_s3(monkeypatch, {("quality", "f.json"): b"s3-data"})
    calls = _spy_local_load_calls(monkeypatch)

    # 로컬에는 다른 내용의 파일을 심어둔다 — 폴백이 실제로 안 탄다면 절대 안 읽혀야 함.
    (tmp_storage / "quality" / "f.json").write_bytes(b"local-data")

    assert storage.load("quality", "f.json") == b"s3-data"
    assert calls == []  # 로컬 폴백 호출이 전혀 없었음


def test_load_falls_back_to_local_when_missing_in_configured_backend(tmp_storage, monkeypatch):
    """설정된 백엔드에 없고 로컬에 있으면 로컬 데이터를 반환한다."""
    _force_fake_s3(monkeypatch, {})
    (tmp_storage / "quality" / "f.json").write_bytes(b"local-only")

    assert storage.load("quality", "f.json") == b"local-only"


def test_load_returns_none_when_missing_everywhere(tmp_storage, monkeypatch):
    """설정된 백엔드/로컬 둘 다 없으면 None."""
    _force_fake_s3(monkeypatch, {})
    assert storage.load("quality", "nowhere.json") is None


# ===== exists() 폴백 시나리오 =====

def test_exists_true_from_configured_backend_without_local_lookup(tmp_storage, monkeypatch):
    fake = _force_fake_s3(monkeypatch, {("quality", "f.json"): b"s3-data"})

    # 로컬 exists 가 호출되면 True 를 리턴하는 함정을 심어, 호출 안 됐는지 확인
    called = []

    def _spy(kind, name):
        called.append((kind, name))
        return True

    monkeypatch.setattr(storage._LOCAL, "exists", _spy)

    assert storage.exists("quality", "f.json") is True
    assert called == []


def test_exists_falls_back_to_local(tmp_storage, monkeypatch):
    _force_fake_s3(monkeypatch, {})
    (tmp_storage / "quality" / "f.json").write_bytes(b"local-only")

    assert storage.exists("quality", "f.json") is True


def test_exists_false_when_missing_everywhere(tmp_storage, monkeypatch):
    _force_fake_s3(monkeypatch, {})
    assert storage.exists("quality", "nowhere.json") is False


# ===== save() — 폴백 없이 설정된 백엔드에만 쓴다 =====

def test_save_writes_only_to_configured_backend_not_local(tmp_storage, monkeypatch):
    fake = _force_fake_s3(monkeypatch, {})

    storage.save("quality", "meta.json", b"{}")

    assert fake.data[("quality", "meta.json")] == b"{}"
    assert not (tmp_storage / "quality" / "meta.json").exists()


# ===== STORAGE_BACKEND=local 회귀 확인 =====

def test_local_backend_single_lookup_no_double_query(tmp_storage, monkeypatch):
    """BACKEND 가 _LOCAL 그 자체일 때는 이중 조회 없이 한 번만 조회한다."""
    monkeypatch.setattr(storage, "BACKEND", storage._LOCAL)
    calls = _spy_local_load_calls(monkeypatch)

    storage.save("quality", "only.json", b"{}")
    assert storage.load("quality", "only.json") == b"{}"
    assert len(calls) == 1  # BACKEND.load 호출 한 번 뿐, 폴백으로 인한 재호출 없음

    calls.clear()
    assert storage.load("quality", "ghost.json") is None
    assert len(calls) == 1  # 못 찾아도 폴백 재호출 없이 한 번만


# ===== delete() =====

def test_local_delete_removes_file(tmp_storage, monkeypatch):
    _force_local(monkeypatch)
    storage.save("quality", "d.json", b"{}")
    storage.delete("quality", "d.json")
    assert not (tmp_storage / "quality" / "d.json").exists()
    assert storage.load("quality", "d.json") is None


def test_local_delete_missing_is_noop(tmp_storage, monkeypatch):
    _force_local(monkeypatch)
    storage.delete("quality", "never.json")          # 예외 없음
    storage.delete("quality", "never.json")          # 두 번 불러도


def test_local_delete_rejects_path_escape(tmp_storage, monkeypatch):
    import pytest
    _force_local(monkeypatch)
    victim = tmp_storage.parent / "outside.txt"
    victim.write_bytes(b"keep")
    with pytest.raises(ValueError):
        storage.delete("quality", "../../outside.txt")
    assert victim.read_bytes() == b"keep"


def test_delete_with_s3_backend_also_removes_local_copy(tmp_storage, monkeypatch):
    """S3 에서만 지우면 load 의 로컬 폴백으로 옛 파일이 되살아난다."""
    fake = _force_fake_s3(monkeypatch, {("quality", "f.json"): b"s3"})
    (tmp_storage / "quality" / "f.json").write_bytes(b"local")

    storage.delete("quality", "f.json")

    assert ("quality", "f.json") not in fake.data
    assert not (tmp_storage / "quality" / "f.json").exists()
    assert storage.load("quality", "f.json") is None
    assert storage.exists("quality", "f.json") is False


def test_delete_with_s3_backend_local_only_copy(tmp_storage, monkeypatch):
    _force_fake_s3(monkeypatch, {})
    (tmp_storage / "quality" / "f.json").write_bytes(b"local")
    storage.delete("quality", "f.json")
    assert storage.load("quality", "f.json") is None


def test_delete_with_s3_backend_missing_everywhere(tmp_storage, monkeypatch):
    _force_fake_s3(monkeypatch, {})
    storage.delete("quality", "nowhere.json")        # 예외 없음


def test_s3_backend_delete_calls_delete_object_with_prefixed_key():
    calls = []

    class FakeClient:
        def delete_object(self, **kw):
            calls.append(kw)

    b = storage.S3Backend.__new__(storage.S3Backend)   # boto3 클라이언트 생성 우회
    b.bucket, b.prefix, b.s3 = "bkt", "pre", FakeClient()
    b.delete("quality", "x.json")
    assert calls == [{"Bucket": "bkt", "Key": "pre/quality/x.json"}]


def test_delete_s3_error_propagates(tmp_storage, monkeypatch):
    """storage.delete 는 백엔드 오류를 삼키지 않는다 — 삼키는 건 호출부(pipeline._clear_quality)."""
    import pytest
    fake = _force_fake_s3(monkeypatch, {})

    def boom(kind, name):
        raise PermissionError("AccessDenied")
    fake.delete = boom
    with pytest.raises(PermissionError):
        storage.delete("quality", "f.json")


def test_local_delete_only_target_file(tmp_storage, monkeypatch):
    _force_local(monkeypatch)
    storage.save("quality", "a.json", b"{}")
    storage.save("quality", "a_inspect.json", b"{}")
    storage.delete("quality", "a.json")
    assert storage.load("quality", "a_inspect.json") == b"{}"
