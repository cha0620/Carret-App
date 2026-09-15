"""스토리지 계층 — local FS 또는 S3 (설정으로 전환).

경로 규칙: 로컬 백엔드의 유일한 기준점은 BASE(=settings.storage_dir) 하나뿐이다.
images.py 의 업로드 저장, main.py 의 `/storage` StaticFiles 마운트, dev.py/
pipeline.py 의 파일 직접 접근이 전부 이 BASE 를 공유해야 서빙이 깨지지 않는다.
"""
from pathlib import Path
import logging

import boto3
from botocore.exceptions import ClientError

from app.core.config import settings
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


class S3Backend:
    def __init__(self):
        self.bucket = settings.s3_bucket
        self.prefix = settings.s3_prefix
        self.s3 = boto3.client(
            "s3",
            region_name=settings.aws_region,
            aws_access_key_id=settings.aws_access_key_id or None,
            aws_secret_access_key=settings.aws_secret_access_key or None,
        )

    def _key(self, kind: str, name: str) -> str:
        return f"{self.prefix}/{kind}/{name}"

    def save(self, kind: str, name: str, data: bytes) -> None:
        self.s3.put_object(Bucket=self.bucket, Key=self._key(kind, name), Body=data)

    def load(self, kind: str, name: str) -> bytes | None:
        try:
            r = self.s3.get_object(Bucket=self.bucket, Key=self._key(kind, name))
            return r["Body"].read()
        except ClientError:
            return None

    def exists(self, kind: str, name: str) -> bool:
        try:
            self.s3.head_object(Bucket=self.bucket, Key=self._key(kind, name))
            return True
        except ClientError:
            return False


_LOCAL = LocalBackend()   # 읽기 폴백 전용 — 백엔드 전환 전 저장된 로컬 fixture 접근용


def _backend():
    if settings.storage_backend == "s3" and settings.s3_bucket:
        return S3Backend()
    return _LOCAL


BACKEND = _backend()


# ===== 모듈 레벨 API (기존 호출부 무수정) =====
def save(kind: str, name: str, data: bytes) -> None:
    if kind in IMAGE_KINDS:
        raw = len(data)
        data = img_util.normalize(data)
        logger.info(f"[storage] {kind}/{name} {raw}→{len(data)}B")
    BACKEND.save(kind, name, data)   # 쓰기는 항상 설정된 백엔드로만


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


def load_original(file_id: str) -> bytes | None:
    """원본 바이트 (확장자 탐색). S3/local 공통."""
    for ext in (".jpg", ".jpeg", ".png", ".webp"):
        data = load("original", f"{file_id}{ext}")
        if data is not None:
            return data
    return None


def original_of(file_id: str) -> Path | None:
    """원본 파일 경로. 로컬 전용(dev 도구/파이프라인이 파일시스템을 직접 다룰 때 사용).

    file_id는 스키마가 hex32 를 보증하므로 glob 이 안전하다 ✅
    """
    hits = list((BASE / "original").glob(f"{file_id}.*"))
    return hits[0] if hits else None


def result_url(file_id: str, preset: str) -> str:
    """브라우저에서 바로 보는 주소 (로컬 백엔드 기준, `/storage` 마운트와 짝)."""
    return f"/storage/result/{file_id}_{preset}.jpg"
