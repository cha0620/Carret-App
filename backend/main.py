from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import images, transform
from app.core.config import settings

app = FastAPI(title="SellerShot API", version="0.1.0")

# CORS (프론트 연동용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # MVP 단계, 나중에 도메인 지정
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(images.router, prefix="/api/images", tags=["images"])
app.include_router(transform.router, prefix="/api", tags=["transform"])

# 프론트 정적 파일 서빙 (MVP 간편 모드)
app.mount("/", StaticFiles(directory="../frontend", html=True), name="frontend")


@app.get("/health")
def health():
    return {"status": "ok"}