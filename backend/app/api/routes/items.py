"""여러 각도 업로드 (10-01) — 사진 여러 장 · 동영상. 10-04 부터 사진 속 물건별로 묶는다.

  POST /api/items                   사진·동영상 여러 개 → 새 묶음 (물건별로 묶기 + 각도 + 빠진 면)
  POST /api/items/{item_id}/files   더 찍어 올리기 → 같은 묶음에 추가하고 다시 분류 (사용자가 고친 묶음은 지킨다)
  PUT  /api/items/{item_id}/objects 사용자가 확인 · 고친 묶음 (물건 이름 · 설명 · 팔지 않음 · 사진 옮기기 · 각도)
  POST /api/items/{item_id}/arrange 파는 물건 여러 개를 한 장에 (나란히 · 격자 · 살짝 겹쳐서 — 오려서 코드로 놓는다)
  GET  /api/items/{item_id}

물건은 팔 물건(product)과 근거 사진(proof — 설명서 · 보증서 · 회로 · 상태 화면)으로 나눈다 (services/listing.py).

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
from app.prompts.presets import PRESETS
from app.schemas.image import ArrangeRequest, ArrangeResponse, ItemObjectsUpdate, ItemResponse
from app.services import coverage, listing, pipeline, video
from app.services.ai import compositor, detector
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
# 배치는 오리기(fal, 유료 · 수 초)를 물건 수만큼 부른다 — 동시에 너무 많이 돌지 않게 (프로세스 안에서만)
_ARRANGE_SLOTS = threading.BoundedSemaphore(2)


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


def _ingest(files: list[UploadFile], room: int, proof_files: list[UploadFile] = ()) -> list[dict]:
    """업로드 → 저장한 사진 목록. 장수는 읽기 전에, 내용은 저장 전에 검사하고, 저장 중 실패하면 지운다.
    proof_files = 근거 칸(보증서 · 정품 마크 · 영수증 …, 10-05) — 사진만. 사진마다 slot 이 product | proof 로
    남고, 묶기(listing.normalize)가 그 칸대로 물건 종류를 정한다."""
    proof_files = list(proof_files or [])
    if any(FsPath(f.filename or "").suffix.lower() in VIDEO_EXTENSIONS for f in proof_files):
        raise HTTPException(400, "근거 칸에는 사진만 올려 주세요")
    files = list(files or [])
    n_product = len(files)                       # 앞은 상품 칸, 뒤는 근거 칸 (위치로 칸을 정한다)
    files += proof_files
    if not files:
        raise HTTPException(400, "사진이나 동영상을 올려 주세요")
    if room <= 0 or len(files) > room:
        raise HTTPException(400, f"사진은 물건 하나에 {settings.max_item_photos}장까지예요 (근거 사진 포함)")
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
            by_pos[pos] = [(fr, "video", f"{f.filename}#{i}", "product") for i, fr in enumerate(frames)]
        else:
            _check_image(data, f.filename)
            by_pos[pos] = [(data, "photo", f.filename, "proof" if pos >= n_product else "product")]
    staged = [x for pos in range(len(files)) for x in by_pos[pos]]
    saved = []
    try:
        for data, source, name, slot in staged:
            file_id = uuid.uuid4().hex
            size = storage.save("original", f"{file_id}.jpg", data)   # 저장하며 JPEG 로 통일 (EXIF 회전 적용)
            saved.append({"file_id": file_id, "source": source, "slot": slot})
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
    """묶음 전체를 다시 분류 — 사진이 늘면 묶음 · 종류 판단도 나아질 수 있어 매번 전부 본다 (low 해상도라 싸다).
    사용자가 고친 묶음은 listing.apply 가 지킨다. 실패하면 앞서 분류한 사진의 값은 두고 새 사진만
    "모름"으로 (일시적 429 로 기존 분류가 지워지지 않게)."""
    photos = item["photos"]
    images = [storage.load_original(p["file_id"]) for p in photos]
    known = [(p, img) for p, img in zip(photos, images) if img is not None]
    try:
        if not known:
            raise ValueError("원본이 하나도 없다")
        out = detector.group_objects([img for _, img in known], [p.get("slot") for p, _ in known])
    except Exception:
        logger.exception("물건 묶기 실패")
        for p in item["photos"]:
            if p["file_id"] in new_ids or "view" not in p:
                p.update(object=None, view=None, occluded=False, blurry=False, item_visible=True,
                         unclassified=True)              # 다음에 묶을 때 새 사진처럼 다시 본다
        item.setdefault("objects", [])                   # 옛 묶음(objects 없음)으로 오해받지 않게
        item["views_failed"] = True
        return item
    known_item = {"photos": [p for p, _ in known], "objects": item.get("objects") or [],
                  "user_edited": item.get("user_edited", False)}
    listing.apply(known_item, out, new_ids)
    ids = {o["id"] for o in known_item["objects"]}
    for p in item["photos"]:                             # 원본을 못 읽은 사진이 사라진 물건을 가리키지 않게
        if p.get("object") not in ids:
            p["object"] = None
    item.update(objects=known_item["objects"], views_failed=False,
                needs_review=known_item.get("needs_review", item.get("needs_review", False)))
    return item


def _state_label(item: dict, p: dict) -> str | None:
    sub = next((o.get("subtype") for o in item.get("objects") or [] if o["id"] == p.get("object")), None)
    return coverage.STATES.get(sub or "", {}).get(p.get("state"))


def _response(item: dict) -> ItemResponse:
    listing.ensure_objects(item)
    photos = item["photos"]
    rows = listing.summary(item)
    main = listing.main_object(rows)
    failed = item.get("views_failed", False)
    product_ids = {r["id"] for r in rows if r["kind"] == "product" and r["for_sale"]}   # 파는 물건 사진만
    retake = [{"file_id": p["file_id"], "reason": " · ".join(coverage.problems(p))}
              for p in photos if p.get("object") in product_ids and coverage.problems(p)]
    return ItemResponse(
        item_id=item["item_id"], item=main["name"] if main else item.get("item", "object"),
        category=main["category"] if main else item.get("category", "other"),
        photos=[{**p, "url": f"/storage/original/{p['file_id']}.jpg",
                 "view_label": coverage.VIEWS.get(p.get("view")),
                 "state_label": _state_label(item, p)} for p in photos],
        missing=[] if failed or not main else main["missing"],
        retake=retake,
        complete=bool(main) and main["complete"] and not retake and not failed,
        views_failed=failed,
        compositions=main["compositions"] if main else [],
        objects=rows, main_object=main["id"] if main else None,
        user_edited=item.get("user_edited", False), needs_review=item.get("needs_review", False))


@router.post("/items", response_model=ItemResponse)
def create_item(files: list[UploadFile] = File(default=[]), proof_files: list[UploadFile] = File(default=[])):
    # def = 스레드풀 (동영상 디코딩 · VLM 호출이 블로킹). proof_files = 근거 칸 (보증서 · 정품 마크 · 영수증 …)
    if not files and proof_files:
        raise HTTPException(400, "상품 사진을 먼저 올려 주세요 — 근거 사진은 상품에 붙어요")
    new = _ingest(files, settings.max_item_photos, proof_files)
    item = {"item_id": uuid.uuid4().hex, "created": datetime.now().isoformat(timespec="seconds"), "photos": new}
    item = _classify(item, {p["file_id"] for p in new})
    _save(item)
    return _response(item)


@router.post("/items/{item_id}/files", response_model=ItemResponse)
def add_files(item_id: str = Path(pattern=ITEM_ID), files: list[UploadFile] = File(default=[]),
              proof_files: list[UploadFile] = File(default=[])):
    with _lock(item_id):
        item = _load(item_id)
        if item is None:
            raise HTTPException(404, "물건을 찾을 수 없습니다")
        new = _ingest(files, settings.max_item_photos - len(item["photos"]), proof_files)
        item["photos"] += new
        item = _classify(item, {p["file_id"] for p in new})
        _save(item)
    return _response(item)


@router.put("/items/{item_id}/objects", response_model=ItemResponse)
def update_objects(body: ItemObjectsUpdate, item_id: str = Path(pattern=ITEM_ID)):
    with _lock(item_id):
        item = _load(item_id)
        if item is None:
            raise HTTPException(404, "물건을 찾을 수 없습니다")
        listing.ensure_objects(item)
        errors = listing.edit(item, body.model_dump())
        if errors:
            raise HTTPException(400, " / ".join(errors[:5]))
        _save(item)
    return _response(item)


@router.post("/items/{item_id}/arrange", response_model=ArrangeResponse)
def arrange_objects(body: ArrangeRequest, item_id: str = Path(pattern=ITEM_ID)):
    """여러 물건을 한 장에 — 생성하지 않는다 (개수 · 포장을 바꾸지 않게, study 10-03 §11-3). 그 사진을 미리 분석해 둔
    값이 있으면 물건 박스(오리기 범위)와 안 파는 물건 박스(지움)를 쓰고, 없으면 사진 전체에서 오린다 — 여기서 VLM 을
    부르지 않는다. 물건 하나(켤레 아님)면 가장 큰 덩어리만 — 같이 찍힌 다른 물건이 두 번 나오지 않게."""
    item = _load(item_id)
    if item is None:
        raise HTTPException(404, "물건을 찾을 수 없습니다")
    listing.ensure_objects(item)
    rows = {r["id"]: r for r in listing.summary(item)}
    if len(set(body.objects)) != len(body.objects):
        raise HTTPException(400, "같은 물건을 두 번 놓을 수 없어요")
    parts, used = [], []
    for oid in body.objects:
        row = rows.get(oid)
        if row is None or row["kind"] != "product" or not row["for_sale"]:
            raise HTTPException(400, f"파는 물건이 아니에요: {oid}")
        fid = body.photos.get(oid) or listing.best_photo(row, item["photos"])
        if fid not in row["photo_ids"]:
            raise HTTPException(400, f"{row['label']} 의 사진이 아니에요")
        original = storage.load_original(fid)
        if original is None:
            raise HTTPException(404, f"사진을 찾을 수 없어요: {row['label']}")
        a = pipeline.load_analysis(fid) or {}
        objs = [o for o in a.get("objects") or [] if isinstance(o, dict) and o.get("box")]
        parts.append({"image": original, "box": a.get("item_box"),
                      "drop": [o["box"] for o in objs if not o.get("for_sale")],
                      "keep": [o["box"] for o in objs if o.get("for_sale")],
                      "single": row.get("count", 1) == 1 and row.get("category") != "shoes"})
        used.append(fid)
    if not _ARRANGE_SLOTS.acquire(blocking=False):
        raise HTTPException(429, "다른 배치를 만드는 중이에요 — 잠시 뒤 다시 눌러 주세요")
    try:
        out = compositor.arrange(parts, PRESETS["studio_white"]["bg_color"], body.layout)
    except ValueError as e:
        raise HTTPException(422, f"물건을 오리지 못했어요: {e}")
    finally:
        _ARRANGE_SLOTS.release()
    name = f"{item_id}_arrange_{body.layout}_{uuid.uuid4().hex[:8]}.jpg"
    storage.save("result", name, out)
    return ArrangeResponse(result_url=f"/storage/result/{name}", photo_ids=used)


@router.get("/items/{item_id}", response_model=ItemResponse)
def get_item(item_id: str = Path(pattern=ITEM_ID)):
    item = _load(item_id)
    if item is None:
        raise HTTPException(404, "물건을 찾을 수 없습니다")
    return _response(item)
