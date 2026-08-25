from pathlib import Path

from app.core.config import settings
from app.util import img_util
import logging

IMAGE_KINDS = {"original", "result"}   # 정규화 대상 (quality json 등은 제외)

logger = logging.getLogger("carret.storage")

BASE = Path(settings.storage_dir)   # "./storage"


def save(kind: str, name: str, data: bytes) -> Path:
    if kind in IMAGE_KINDS:
        raw = len(data)
        data = img_util.normalize(data)
        logger.info(f"[storage] {kind}/{name} {raw}→{len(data)}B")
    path = BASE / kind / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def original_of(file_id: str) -> Path | None:
    """원본 찾기. (file_id는 스키마가 hex32 보증 → glob 안전 ✅)"""
    hits = list((BASE / "original").glob(f"{file_id}.*"))
    return hits[0] if hits else None


def result_url(file_id: str, preset: str) -> str:
    """브라우저에서 바로 보는 주소."""
    return f"/storage/result/{file_id}_{preset}.jpg"