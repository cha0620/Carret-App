# app/services/imageutil.py
import io
from PIL import Image, ImageOps

from app.core.config import settings

def downscale(image_bytes: bytes, max_side: int = 1024) -> bytes:
    img = Image.open(io.BytesIO(image_bytes))
    img.thumbnail((max_side, max_side))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()

MAX_PIXELS = 60_000_000   # 약 7700x7700 — 휴대폰 사진(최대 2억 화소 모드 제외)은 들어온다


def normalize(data: bytes, max_side: int | None = None) -> bytes:
    """크기 상한 + JPEG 통일. 축소만 하고 확대는 안 함(멱등)."""
    max_side = max_side or settings.image_max_side
    img = Image.open(io.BytesIO(data))
    if img.width * img.height > MAX_PIXELS:   # 작은 파일로 큰 그림을 푸는 압축 폭탄 (10-01 리뷰)
        raise ValueError(f"이미지가 너무 큼: {img.width}x{img.height}")
    # 휴대폰 사진은 픽셀은 누운 채 EXIF 로 회전을 적는다 — JPEG 로 다시 쓰면 EXIF 가 빠지므로 먼저 돌린다
    # (10-01: 여러 각도 업로드가 normalize 를 거치며 세로 사진이 누웠다. 생성은 구도를 유지하니 결과도 누움)
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")                 # RGBA/P 모드 → JPEG 안전
    img.thumbnail((max_side, max_side))      # 크면 줄이고, 작으면 그대로
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=settings.image_quality)
    return buf.getvalue()