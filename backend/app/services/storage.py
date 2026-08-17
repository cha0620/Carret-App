"""저장소 - 단순 하드코딩 버전.
원칙: 일단 되게. 이상해지면 그때 리팩토링한다.
"""
from pathlib import Path

from app.core.config import settings

BASE = Path(settings.storage_dir)   # "./storage"


def save(stage: str, filename: str, data: bytes) -> Path:
    """storage/{stage}/{filename} 에 저장."""
    folder = BASE / stage
    folder.mkdir(parents=True, exist_ok=True)   # 없으면 만들고, 있어도 말고
    target = folder / filename
    target.write_bytes(data)
    return target


def original_of(file_id: str) -> Path | None:
    """원본 찾기. (file_id는 스키마가 hex32 보증 → glob 안전 ✅)"""
    hits = list((BASE / "original").glob(f"{file_id}.*"))
    return hits[0] if hits else None


def result_url(file_id: str, preset: str) -> str:
    """브라우저에서 바로 보는 주소."""
    return f"/storage/result/{file_id}_{preset}.jpg"