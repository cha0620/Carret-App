from PIL import Image

from app.services.persistence import storage


def _force_local(monkeypatch):
    """.env 의 STORAGE_BACKEND=s3 와 무관하게, storage.BACKEND 모듈 전역을
    강제로 LocalBackend 로 바꿔치기한다. BACKEND 는 모듈 임포트 시점에
    한 번만 만들어지는 전역이라 settings.storage_backend 만 바꿔서는
    이미 만들어진 인스턴스에 영향을 주지 못한다."""
    monkeypatch.setattr(storage, "BACKEND", storage.LocalBackend())


def test_save_caps_big_image(tmp_storage, make_png, monkeypatch):
    _force_local(monkeypatch)
    storage.save("result", "t.jpg", make_png(3000, 2000))
    with Image.open(tmp_storage / "result" / "t.jpg") as im:
        assert max(im.size) <= 1600


def test_save_does_not_normalize_non_image_kind(tmp_storage, monkeypatch):
    """IMAGE_KINDS 에 없는 kind(e.g. 'quality')는 img_util.normalize 를
    타지 않으므로, 이미지가 아닌 바이트를 넣어도 그대로 저장돼야 한다."""
    _force_local(monkeypatch)
    raw = b"not an image, just some bytes"
    storage.save("quality", "meta.json", raw)
    assert (tmp_storage / "quality" / "meta.json").read_bytes() == raw


def test_load_original_prefers_jpg_over_other_extensions(tmp_storage, monkeypatch):
    _force_local(monkeypatch)
    fid = "dupext"
    (tmp_storage / "original" / f"{fid}.png").write_bytes(b"png-bytes")
    (tmp_storage / "original" / f"{fid}.jpg").write_bytes(b"jpg-bytes")
    assert storage.load_original(fid) == b"jpg-bytes"


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


# ===== exists() 폴백 시나리오 =====


# ===== save() — 폴백 없이 설정된 백엔드에만 쓴다 =====

def test_save_writes_only_to_configured_backend_not_local(tmp_storage, monkeypatch):
    fake = _force_fake_s3(monkeypatch, {})

    storage.save("quality", "meta.json", b"{}")

    assert fake.data[("quality", "meta.json")] == b"{}"
    assert not (tmp_storage / "quality" / "meta.json").exists()


# ===== STORAGE_BACKEND=local 회귀 확인 =====


# ===== delete() =====


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


def test_delete_s3_error_propagates(tmp_storage, monkeypatch):
    """storage.delete 는 백엔드 오류를 삼키지 않는다 — 삼키는 건 호출부(pipeline._clear_quality)."""
    import pytest
    fake = _force_fake_s3(monkeypatch, {})

    def boom(kind, name):
        raise PermissionError("AccessDenied")
    fake.delete = boom
    with pytest.raises(PermissionError):
        storage.delete("quality", "f.json")




# ===== list() / list_files() =====

def test_s3_list_uses_paginator_with_delimiter():
    """S3Backend.list — list_objects_v2 paginator 를 Delimiter='/' 로 훑어 {이름: epoch} (가짜 클라이언트)."""
    from datetime import datetime, timezone
    calls = []

    class FakePaginator:
        def paginate(self, **kw):
            calls.append(kw)
            t = datetime(2026, 1, 1, tzinfo=timezone.utc)
            return [{"Contents": [{"Key": "p/result/", "LastModified": t},   # 폴더 마커 — 제외
                                  {"Key": "p/result/a.jpg", "LastModified": t}]},
                    {"Contents": [{"Key": "p/result/b.jpg", "LastModified": t}]}]

    class FakeClient:
        def get_paginator(self, op):
            assert op == "list_objects_v2"
            return FakePaginator()

    b = storage.S3Backend.__new__(storage.S3Backend)
    b.bucket, b.prefix, b.s3 = "bk", "p", FakeClient()
    out = b.list("result")
    assert set(out) == {"a.jpg", "b.jpg"}
    assert out["a.jpg"] == datetime(2026, 1, 1, tzinfo=timezone.utc).timestamp()
    assert calls == [{"Bucket": "bk", "Prefix": "p/result/", "Delimiter": "/"}]


def test_list_files_merges_local_and_backend_wins_on_same_name(tmp_storage, monkeypatch):
    fake = _force_fake_s3(monkeypatch)
    fake.list = lambda kind: {"both.jpg": 200.0, "s3only.jpg": 300.0}
    (tmp_storage / "original" / "both.jpg").write_bytes(b"x")
    (tmp_storage / "original" / "localonly.jpg").write_bytes(b"x")
    out = storage.list_files("original")
    assert set(out) == {"both.jpg", "s3only.jpg", "localonly.jpg"}
    assert out["both.jpg"] == 200.0
