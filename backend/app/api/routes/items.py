"""여러 각도 업로드 (10-01) — 물건 하나에 사진 여러 장 · 동영상.

  POST /api/items                  사진·동영상 여러 개 → 새 물건 묶음 (각도 분류 + 빠진 면)
  POST /api/items/{item_id}/files  더 찍어 올리기 → 같은 묶음에 추가하고 다시 분류
  GET  /api/items/{item_id}

사진 한 장 한 장은 지금까지와 같은 원본(file_id)이다 — 사용자가 원하는 구도의 사진을 골라
기존 /api/transform 에 그 file_id 를 넘긴다. 구도를 모델에게 다시 그리게 하지 않는다 (study 10-01).
묶음은 storage 의 items/<item_id>.json 하나 (S3 모드에서도 같은 길).
"""
import io
import json
import logging
import threading
import uuid
from datetime import datetime
from pathlib import Path as FsPath

from fastapi import APIRouter, File, HTTPException, Path, UploadFile
from PIL import Image, UnidentifiedImageError

from app.api.routes.images import ALLOWED_EXTENSIONS
from app.core.config import settings
from app.schemas.image import ItemResponse
from app.services import compositions, coverage, video
from app.services.ai import detector
from app.services.persistence import storage, store
from app.util.img_util import MAX_PIXELS

logger = logging.getLogger("carret.items")
router = APIRouter()
VIDEO_EXTENSIONS = {".mp4", ".mov", ".webm", ".m4v"}
ITEM_ID = r"^[a-f0-9]{32}$"
MAX_VIDEOS = 2                     # 한 요청에 동영상 (디코딩이 CPU 를 오래 잡는다)
# 같은 물건에 동시에 추가하면 읽고-고치고-쓰는 사이에 한쪽 사진이 사라진다 — 물건별 잠금.
# 고정 개수로 나눠 쓴다 (물건마다 하나씩 만들면 지우지 못해 계속 쌓인다). 프로세스 안에서만 지킨다
# (워커가 여럿이면 프론트가 업로드 중 버튼을 막는 것에 기댄다).
_LOCKS = [threading.Lock() for _ in range(64)]


def _lock(item_id: str) -> threading.Lock:
    return _LOCKS[int(item_id[:8], 16) % len(_LOCKS)]   # item_id 는 패턴으로 hex 가 보장된다


def _load(item_id: str) -> dict | None:
    raw = storage.load("items", f"{item_id}.json")
    return json.loads(raw) if raw else None


def _save(item: dict) -> None:
    storage.save("items", f"{item['item_id']}.json", json.dumps(item, ensure_ascii=False).encode())


def _check_image(data: bytes, name: str | None) -> None:
    """저장 전에 열어 본다 — 이름만 .jpg 인 파일 · 압축 폭탄을 저장 전에 거른다 (일부만 저장되지 않게)."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PIXELS:
                raise HTTPException(400, f"이미지가 너무 커요: {name}")
            img.verify()
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError):
        raise HTTPException(400, f"이미지를 읽을 수 없어요: {name}")


def _read_upload(f: UploadFile) -> tuple[str, bytes]:
    ext = FsPath(f.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS | VIDEO_EXTENSIONS:
        raise HTTPException(400, f"지원하지 않는 형식: {ext or '(없음)'} — jpg · png · webp · mp4 · mov · webm")
    limit = settings.max_video_size_mb if ext in VIDEO_EXTENSIONS else settings.max_upload_size_mb
    data = f.file.read(limit * 1024 * 1024 + 1)
    if len(data) > limit * 1024 * 1024:
        raise HTTPException(400, f"파일 크기 초과 (최대 {limit}MB): {f.filename}")
    if not data:
        raise HTTPException(400, f"빈 파일: {f.filename}")
    return ext, data


def _ingest(files: list[UploadFile], room: int) -> list[dict]:
    """업로드 → 저장한 사진 목록. 장수는 읽기 전에, 내용은 저장 전에 검사하고, 저장 중 실패하면 지운다."""
    if not files:
        raise HTTPException(400, "사진이나 동영상을 올려 주세요")
    if room <= 0 or len(files) > room:
        raise HTTPException(400, f"사진은 물건 하나에 {settings.max_item_photos}장까지예요")
    videos = [f for f in files if FsPath(f.filename or "").suffix.lower() in VIDEO_EXTENSIONS]
    if len(videos) > MAX_VIDEOS:
        raise HTTPException(400, f"동영상은 한 번에 {MAX_VIDEOS}개까지 올려 주세요")
    photos = [f for f in files if f not in videos]
    per_video = (room - len(photos)) // len(videos) if videos else 0
    if videos and per_video < 1:
        raise HTTPException(400, f"사진은 물건 하나에 {settings.max_item_photos}장까지예요")
    # 사진을 먼저 검사한다 (싸다) — 깨진 사진이 있으면 동영상을 풀기 전에 거절. 저장 순서는 올린 순서대로
    by_pos: dict[int, list] = {}
    for pos in sorted(range(len(files)), key=lambda k: files[k] in videos):
        f = files[pos]
        ext, data = _read_upload(f)
        if ext in VIDEO_EXTENSIONS:
            try:
                frames = video.extract_frames(data, ext, max_frames=min(8, per_video))
            except video.VideoError as e:
                raise HTTPException(400, str(e))
            by_pos[pos] = [(fr, "video", f"{f.filename}#{i}") for i, fr in enumerate(frames)]
        else:
            _check_image(data, f.filename)
            by_pos[pos] = [(data, "photo", f.filename)]
    staged = [x for pos in range(len(files)) for x in by_pos[pos]]
    saved = []
    try:
        for data, source, name in staged:
            file_id = uuid.uuid4().hex
            size = storage.save("original", f"{file_id}.jpg", data)   # 저장하며 JPEG 로 통일 (EXIF 회전 적용)
            saved.append({"file_id": file_id, "source": source})
            try:
                store.record_original(file_id, ".jpg", f"item_{source}", original_name=name, size_bytes=size)
            except Exception:
                logger.exception("원본 메타데이터 기록 실패(무시)")
    except Exception:
        for p in saved:
            storage.delete("original", f"{p['file_id']}.jpg")
        raise
    return saved


def _classify(item: dict, new_ids: set[str]) -> dict:
    """묶음 전체를 다시 분류 — 사진이 늘면 종류 판단도 나아질 수 있어 매번 전부 본다 (low 해상도라 싸다).
    실패하면 앞서 분류한 사진의 값은 두고 새 사진만 "모름"으로 (일시적 429 로 기존 분류가 지워지지 않게)."""
    photos = item["photos"]
    images = [storage.load_original(p["file_id"]) for p in photos]
    known = [(p, img) for p, img in zip(photos, images) if img is not None]
    try:
        if not known:
            raise ValueError("원본이 하나도 없다")
        out = detector.classify_views([img for _, img in known])
    except Exception:
        logger.exception("각도 분류 실패")
        for p in item["photos"]:
            if p["file_id"] in new_ids or "view" not in p:
                p.update(view=None, occluded=False, blurry=False, item_visible=True)
        item["views_failed"] = True
        return item
    for (p, _), v in zip(known, out["photos"]):
        p.update(v)
    item.update(category=out["category"], item=out["item"], views_failed=False)
    return item


def _response(item: dict) -> ItemResponse:
    photos = item["photos"]
    cov = coverage.check(item.get("category", "other"), photos)
    return ItemResponse(
        item_id=item["item_id"], item=item.get("item", "object"), category=item.get("category", "other"),
        photos=[{**p, "url": f"/storage/original/{p['file_id']}.jpg",
                 "view_label": coverage.VIEWS.get(p.get("view"))} for p in photos],
        missing=[] if item.get("views_failed") else cov["missing"],
        retake=[{"file_id": photos[r["index"]]["file_id"], "reason": r["reason"]} for r in cov["retake"]],
        complete=cov["complete"] and not item.get("views_failed"),
        views_failed=item.get("views_failed", False),
        compositions=compositions.options(item.get("category", "other"), photos))


@router.post("/items", response_model=ItemResponse)
def create_item(files: list[UploadFile] = File(...)):
    # def = 스레드풀 (동영상 디코딩 · VLM 호출이 블로킹)
    new = _ingest(files, settings.max_item_photos)
    item = {"item_id": uuid.uuid4().hex, "created": datetime.now().isoformat(timespec="seconds"), "photos": new}
    item = _classify(item, {p["file_id"] for p in new})
    _save(item)
    return _response(item)


@router.post("/items/{item_id}/files", response_model=ItemResponse)
def add_files(item_id: str = Path(pattern=ITEM_ID), files: list[UploadFile] = File(...)):
    with _lock(item_id):
        item = _load(item_id)
        if item is None:
            raise HTTPException(404, "물건을 찾을 수 없습니다")
        new = _ingest(files, settings.max_item_photos - len(item["photos"]))
        item["photos"] += new
        item = _classify(item, {p["file_id"] for p in new})
        _save(item)
    return _response(item)


@router.get("/items/{item_id}", response_model=ItemResponse)
def get_item(item_id: str = Path(pattern=ITEM_ID)):
    item = _load(item_id)
    if item is None:
        raise HTTPException(404, "물건을 찾을 수 없습니다")
    return _response(item)
