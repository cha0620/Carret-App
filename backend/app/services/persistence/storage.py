"""스토리지 계층 — local FS 또는 S3 (설정으로 전환).

저장 · 읽기 · 목록은 모두 이 모듈을 거친다 (업로드 · 파이프라인 · main.py 의 `/storage` 라우트 ·
dev 목록) — 그래야 STORAGE_BACKEND=s3 에서도 같은 주소로 서빙된다. 로컬 기준점은 BASE(=settings.storage_dir)
하나뿐이고, S3 모드에선 옛 로컬 사본의 읽기 폴백 + dev 인박스(사용자가 파일을 던지는 로컬 폴더)에만 쓴다.
"""
from pathlib import Path
import logging

import boto3
from botocore.exceptions import ClientError

from app.core.config import reveal, settings
from app.util import img_util

IMAGE_KINDS = {"original", "result"}   # 정규화 대상 (quality json 등은 제외)

logger = logging.getLogger("carret.storage")

BASE = Path(settings.storage_dir)   # "./storage" — 로컬 백엔드 + dev 도구 공통 기준


def _safe_path(kind: str, name: str) -> Path:
    """BASE 밖으로 못 나가게 컨테인먼트 검증 (kind/name 이 신뢰 안 되는 입력일 수 있음,
    예: /storage/{kind}/{name} HTTP 라우트)."""
    path = (BASE / kind / name).resolve()
    if not path.is_relative_to(BASE.resolve()):
        raise ValueError(f"경로 이탈 시도: {kind}/{name}")
    return path


class LocalBackend:
    def save(self, kind: str, name: str, data: bytes) -> None:
        path = _safe_path(kind, name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def load(self, kind: str, name: str) -> bytes | None:
        path = _safe_path(kind, name)
        return path.read_bytes() if path.exists() else None

    def exists(self, kind: str, name: str) -> bool:
        return _safe_path(kind, name).exists()

    def delete(self, kind: str, name: str) -> None:
        _safe_path(kind, name).unlink(missing_ok=True)

    def list(self, kind: str) -> dict[str, float]:
        folder = _safe_path(kind, "")
        out = {}
        if folder.is_dir():
            for f in folder.iterdir():
                try:
                    if f.is_file():
                        out[f.name] = f.stat().st_mtime
                except FileNotFoundError:   # 훑는 사이 삭제
                    continue
        return out


def _warn_unless_missing(e: ClientError, op: str, kind: str, name: str) -> None:
    """없음(404)은 조용히, 그 밖(권한 403 · 요청 제한 등)은 로그 — 로컬을 비운 뒤엔 "파일 없음" 404 로만
    보여 장애가 감춰진다 (10-05 리뷰). 동작은 예전처럼 "없음" 으로 둔다."""
    code = str(e.response.get("Error", {}).get("Code", ""))
    if code not in ("404", "NoSuchKey", "NotFound"):
        logger.warning(f"[storage] S3 {op} {kind}/{name} 실패: {code}")


class S3Backend:
    def __init__(self):
        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix
        self.s3 = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=reveal(settings.aws_access_key_id) or None,
            aws_secret_access_key=reveal(settings.aws_secret_access_key) or None,
        )

    def _key(self, kind: str, name: str) -> str:
        return f"{self.prefix}/{kind}/{name}"

    def save(self, kind: str, name: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self._key(kind, name), Body=data)

    def load(self, kind: str, name: str) -> bytes | None:
        try:
            r = self.s3.get_object(Bucket=self.bucket, Key=self._key(kind, name))
            return r["Body"].read()
        except ClientError as e:
            _warn_unless_missing(e, "load", kind, name)
            return None

    def exists(self, kind: str, name: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._key(kind, name))
            return True
        except ClientError as e:
            _warn_unless_missing(e, "exists", kind, name)
            return False

    def delete(self, kind: str, name: str) -> None:
        self.s3.delete_object(Bucket=self.bucket, Key=self._key(kind, name))

    def list(self, kind: str) -> dict[str, float]:
        prefix = self._key(kind, "")
        out = {}
        for page in self.s3.get_paginator("list_objects_v2").paginate(
                Bucket=self.bucket, Prefix=prefix, Delimiter="/"):   # 하위 폴더는 안 내려감 (로컬과 같게)
            for o in page.get("Contents", []):
                name = o["Key"][len(prefix):]
                if name:   # 콘솔이 만든 "폴더 마커"(Key == prefix) 는 뺀다
                    out[name] = o["LastModified"].timestamp()
        return out


_LOCAL = LocalBackend()   # 읽기 폴백 전용 — 백엔드 전환 전 저장된 로컬 fixture 접근용


def _backend():
    if settings.storage_backend == "s3" and settings.s3_bucket:
        return S3Backend()
    return _LOCAL


BACKEND = _backend()


# ===== 모듈 레벨 API (기존 호출부 무수정) =====
def save(kind: str, name: str, data: bytes) -> int:
    """실제로 쓴 바이트 수를 돌려준다 — original/result는 normalize()가 재인코딩하므로
    호출부가 받은 원본 bytes 길이(len(data))와 다르다. size_bytes를 DB에 남기는
    호출부는 원본 길이가 아니라 이 반환값을 써야 실제 저장된 크기와 맞는다."""
    if kind in IMAGE_KINDS:
        raw = len(data)
        data = img_util.normalize(data)
        logger.info(f"[storage] {kind}/{name} {raw}→{len(data)}B")
    BACKEND.save(kind, name, data)   # 쓰기는 항상 설정된 백엔드로만
    return len(data)


def load(kind: str, name: str) -> bytes | None:
    """설정된 백엔드에서 찾고, 없으면 로컬도 뒤진다 —
    AWS/로컬 어디에 저장돼 있든 같은 페어를 돌려받기 위함."""
    data = BACKEND.load(kind, name)
    if data is None and BACKEND is not _LOCAL:
        data = _LOCAL.load(kind, name)
    return data


def exists(kind: str, name: str) -> bool:
    if BACKEND.exists(kind, name):
        return True
    return BACKEND is not _LOCAL and _LOCAL.exists(kind, name)


def delete(kind: str, name: str) -> None:
    """없어도 조용히 끝난다. load 가 로컬로 폴백하므로 로컬 사본도 지운다
    (안 지우면 S3 에서 지운 파일이 로컬 폴백으로 되살아난다)."""
    BACKEND.delete(kind, name)
    if BACKEND is not _LOCAL:
        _LOCAL.delete(kind, name)


def list_files(kind: str) -> dict[str, float]:
    """kind 폴더 바로 아래 파일 {이름: 수정 시각(epoch)}. load 처럼 로컬 사본도 합친다
    (같은 이름이면 설정된 백엔드 쪽). dev 도구의 목록용 — 운영 경로는 쓰지 않는다."""
    out = _LOCAL.list(kind) if BACKEND is not _LOCAL else {}
    return {**out, **BACKEND.list(kind)}


def load_original(file_id: str) -> bytes | None:
    """원본 바이트 (확장자 탐색). S3/local 공통."""
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        data = load("original", f"{file_id}{ext}")
        if data is not None:
            return data
    return None


def result_url(file_id: str, preset: str) -> str:
    """브라우저에서 바로 보는 주소 — main.py 의 `/storage` 라우트가 storage 를 거쳐 서빙 (local/s3 같은 주소)."""
    return f"/storage/result/{file_id}_{preset}.jpg"
