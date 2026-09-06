import uuid
import io
from pathlib import Path
from fastapi import HTTPException
from PIL import Image
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from app.core.config import settings
from app.services import storage
router = APIRouter()   # 엔드포인트 묶음 (main.py에 include 됨)
from app.schemas.image import UploadResponse
import httpx
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}  # ✅ 허용 목록 방식 (보안 기본기)

class UrlUploadRequest(BaseModel):
    url: str


@router.post("/upload-url")
async def upload_url(req: UrlUploadRequest):
    # 0) 기본 방어
    if not req.url.startswith(("http://", "https://")):
        raise HTTPException(400, "http(s) URL만 가능")

    # 1) 다운로드
    async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
        r = await c.get(req.url)
    if r.status_code != 200:
        raise HTTPException(400, f"다운로드 실패: {r.status_code}")
    if len(r.content) > settings.max_bytes:
        raise HTTPException(413, "이미지가 너무 큽니다")

    # 2) 이미지 검증 + 확장자 판별
    try:
        img = Image.open(io.BytesIO(r.content))
        ext = (img.format or "JPEG").lower()
    except Exception:
        raise HTTPException(400, "이미지가 아닙니다")

    # 3) 기존 업로드와 동일하게 저장
    file_id = uuid.uuid4().hex
    storage.save("original", f"{file_id}.{ext}", r.content)
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

    # 3️⃣ 저장 (이름을 uuid로 바꾸는 게 핵심 ✅)
    save_dir = Path(settings.storage_dir) / "original"
    save_dir.mkdir(parents=True, exist_ok=True)  # 폴더 없으면 만들고, 있어도 에러 없음

    file_id = uuid.uuid4().hex   # 예측 불가능한 고유 이름 (보안+중복방지)
    save_path = save_dir / f"{file_id}{ext}"
    save_path.write_bytes(content)

    # 4️⃣ 응답 (file_id가 이후 변환 요청의 열쇠 🔑)
    return {
        "file_id": file_id,
        "filename": file.filename,
        "size_mb": round(size_mb, 2),
    }