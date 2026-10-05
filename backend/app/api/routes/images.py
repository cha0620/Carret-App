import logging
import uuid
from pathlib import Path
from fastapi import HTTPException
from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel
from app.core.config import settings
from app.services.persistence import storage, store
from app.util.img_fetch import fetch_image
router = APIRouter()   # 엔드포인트 묶음 (main.py에 include 됨)
from app.schemas.image import UploadResponse

logger = logging.getLogger("carret")
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}  # ✅ 허용 목록 방식 (보안 기본기)

def _save_original(file_id: str, data: bytes) -> int:
    """원본 저장 — storage 가 JPEG 로 다시 쓰므로 이름도 .jpg 로 통일 (items.py 와 같게, 10-05).
    깨진 이미지 · 확장자만 바꾼 파일 · 압축 폭탄은 400 (normalize 의 예외가 500 으로 새지 않게)."""
    try:
        return storage.save("original", f"{file_id}.jpg", data)
    except (OSError, ValueError) as e:   # PIL UnidentifiedImageError ⊂ OSError, 너무 큰 그림 = ValueError
        raise HTTPException(status_code=400, detail="이미지를 읽을 수 없습니다") from e


class UrlUploadRequest(BaseModel):
    url: str


@router.post("/upload-url")
async def upload_url(req: UrlUploadRequest):
    data, _ = await fetch_image(req.url)
    file_id = uuid.uuid4().hex
    size_bytes = _save_original(file_id, data)
    try:
        store.record_original(file_id, ".jpg", "upload_url",
                               original_name=req.url, size_bytes=size_bytes)
    except Exception:
        # 파일은 이미 저장됐고 file_id도 응답해야 하니, 메타데이터 기록 실패로
        # 업로드 자체를 실패시키지 않는다.
        logger.exception("원본 메타데이터 기록 실패(무시)")
    return {"file_id": file_id}

@router.post("/upload", response_model=UploadResponse)
# 최종 주소: prefix("/api/images") + "/upload" = /api/images/upload
async def upload_image(file: UploadFile = File(...)):
    # File(...) = "이 요청엔 파일이 반드시 있어야 함" (없으면 자동 422)

    # 1️⃣ 확장자 검증
    ext = Path(file.filename).suffix.lower()   # ".JPG" → ".jpg" 정규화
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"지원하지 않는 형식: {ext}")

    # 2️⃣ 읽기 + 크기 검증 (저장 "전에" 검사하는 게 포인트 ✅)
    content = await file.read()                # async로 파일 바이트 읽기
    size_mb = len(content) / (1024 * 1024)
    if size_mb > settings.max_upload_size_mb:
        raise HTTPException(status_code=400, detail="파일 크기 초과 (최대 10MB)")

    # 3️⃣ 저장 (이름을 uuid로 바꾸는 게 핵심 ✅) — storage 를 거친다 (STORAGE_BACKEND=s3 면 S3 로).
    # 예전엔 로컬 디스크에 직접 써서 S3 모드에서도 로컬 storage/original 이 쌓였다 (10-05)
    file_id = uuid.uuid4().hex   # 예측 불가능한 고유 이름 (보안+중복방지)
    size_bytes = _save_original(file_id, content)
    try:
        store.record_original(file_id, ".jpg", "upload",
                               original_name=file.filename, size_bytes=size_bytes)
    except Exception:
        logger.exception("원본 메타데이터 기록 실패(무시)")

    # 4️⃣ 응답 (file_id가 이후 변환 요청의 열쇠 🔑)
    return {
        "file_id": file_id,
        "filename": file.filename,
        "size_mb": round(size_mb, 2),
    }