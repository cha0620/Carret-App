# app/services/imageutil.py
import io
from PIL import Image

from app.core.config import settings

def downscale(image_bytes: bytes, max_side: int = 1024) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()

def normalize(data: bytes, max_side: int | None = None) -> bytes:
    """크기 상한 + JPEG 통일. 축소만 하고 확대는 안 함(멱등)."""
    max_side = max_side or settings.image_max_side
    img = Image.open(io.BytesIO(data))
    img = img.convert("RGB")                 # RGBA/P 모드 → JPEG 안전
    img.thumbnail((max_side, max_side))      # 크면 줄이고, 작으면 그대로
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=settings.image_quality)
    return buf.getvalue()