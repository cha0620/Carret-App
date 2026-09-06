from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import images
from app.api.routes import transform
from app.core.config import settings

import time
from app.core.logsetup import setup_logging
import logging

setup_logging()
logger = logging.getLogger("carret")
logging.getLogger("httpx").setLevel(logging.WARNING)      # 폴링 잡담 끔
logging.getLogger("google_genai").setLevel(logging.WARNING)

app = FastAPI(title="SellerShot API", version="0.1.0")

# CORS (프론트 분리용)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # MVP 단계, 나중에 도메인 지정
    allow_methods=["*"],
    allow_headers=["*"],
)

# 1) API 라우터
app.include_router(images.router, prefix="/api/images", tags=["images"])
app.include_router(transform.router, prefix="/api", tags=["transform"])

# 2) 결과 파일 서빙
app.mount("/storage", StaticFiles(directory=settings.storage_dir), name="storage")

if settings.dev_tools:
    from app.api.routes import dev
    app.include_router(dev.router, prefix="/dev", tags=["dev"])
    
# 3) ⭐ 프론트 (반드시 맨 마지막)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")

@app.middleware("http")
async def log_requests(request, call_next):
    t0 = time.time()
    resp = await call_next(request)
    logger.info(f"{request.method} {request.url.path} "
                f"→ {resp.status_code} ({time.time()-t0:.2f}s)")
    return resp