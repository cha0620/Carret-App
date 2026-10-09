import mimetypes
import re
from pathlib import Path

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.routes import feedback
from app.api.routes import images
from app.api.routes import items
from app.api.routes import transform
from app.core import db
from app.core.config import settings
from app.services.persistence import storage

import time
from app.core.logsetup import setup_logging
import logging

setup_logging()
logger = logging.getLogger("carret")
logging.getLogger("httpx").setLevel(logging.WARNING)      # 폴링 잡담 끔
logging.getLogger("google_genai").setLevel(logging.WARNING)

app = FastAPI(title="SellerShot API", version="0.1.0")

db.init_db()


@app.on_event("startup")
def _warmup():
    from app.core.warmup import start_warmup
    start_warmup()   # 첫 요청이 모델 로딩(20초 안팎)을 기다리지 않게 — 백그라운드

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
app.include_router(feedback.router, prefix="/api", tags=["feedback"])
app.include_router(items.router, prefix="/api", tags=["items"])

# 2) 결과 파일 서빙 — storage 추상화를 거친다 (STORAGE_BACKEND=local/s3 무관하게 동일 URL로 서빙)
# 이미지(original · result)만 — quality(성적표 · inspect · 분석 JSON)는 dev API 로만 읽는다 (10-05)
SERVED_KINDS = {"original", "result"}
# 파이프라인이 만드는 이름만 (file_id hex32 + 선택적 _preset/_layout + 이미지 확장자) — S3 GET 남용 · 이상한 키 차단
SERVED_NAME = re.compile(r"[0-9a-f]{32}(_[A-Za-z0-9_-]+)?\.(jpg|jpeg|png|webp)")


@app.get("/storage/{kind}/{name:path}")
def serve_storage(kind: str, name: str):
    if kind not in SERVED_KINDS or not SERVED_NAME.fullmatch(name):
        raise HTTPException(404, "파일 없음")
    data = storage.load(kind, name)
    if data is None:
        raise HTTPException(404, "파일 없음")
    media_type = mimetypes.guess_type(name)[0] or "application/octet-stream"
    return Response(content=data, media_type=media_type)

if settings.dev_tools:
    from app.api.routes import dev
    app.include_router(dev.router, prefix="/dev", tags=["dev"])
    
# 3) ⭐ 프론트 (반드시 맨 마지막)
FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"
if FRONTEND_DIR.exists():
    Path(settings.storage_dir).mkdir(parents=True, exist_ok=True)
    class _FreshStatic(StaticFiles):
        """화면 파일은 매번 새로 확인하게 (10-09) — 고친 JS 가 브라우저 캐시 때문에 안 보였다. ETag 로 안 바뀌면 304."""
        async def get_response(self, path, scope):
            resp = await super().get_response(path, scope)
            if path == "" or path.endswith((".html", ".js", ".css")) or "." not in path.rsplit("/", 1)[-1]:
                resp.headers["Cache-Control"] = "no-cache"   # 이미지 · 아이콘은 그대로 캐시 (10-09 리뷰)
            return resp

    app.mount("/", _FreshStatic(directory=FRONTEND_DIR, html=True), name="frontend")

@app.middleware("http")
async def log_requests(request, call_next):
    t0 = time.time()
    resp = await call_next(request)
    logger.info(f"{request.method} {request.url.path} "
                f"→ {resp.status_code} ({time.time()-t0:.2f}s)")
    return resp