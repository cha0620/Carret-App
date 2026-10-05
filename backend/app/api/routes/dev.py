"""개발 도구 — 스테이지 단위 테스트 API (dev 전용).

원칙: 이 모듈은 로직을 "소유하지 않는다".
모든 계산은 프로덕션 단일 진실원(detector/pipeline) 에 위임,
여기서는 "어떤 입력을 줄지" 만 선택한다.
= 테스트는 프로덕션을 호출하지, 복사하지 않는다.
"""
import json
import logging
import re
import threading
import uuid
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.prompts.presets import get_preset
from app.services import ingest, pipeline
from app.services.ai import auto_feedback, detector
from app.services.persistence import storage, store
from app.util.img_fetch import fetch_image

router = APIRouter()

INBOX_EXTS = {".jpg", ".jpeg", ".png", ".webp"}
_inbox_lock = threading.Lock()   # 동시 호출 방지 — fal.ai/VLM 실호출이 겹치면 같은 사진이 두 번 돌아 비용이 두 배로 나간다


# ---- 요청 스키마 (이 라우터의 계약) ----
class DevPairReq(BaseModel):
    file_id: str
    preset: str


class DevRunInboxReq(BaseModel):
    preset: str = "studio_white"
    urls: list[str] = Field(default_factory=list)


# ---- 입력 해결사 (dev 유일의 소유 코드: 입력 선택 + 404) ----
# storage.load*() 를 거친다 — local/S3 백엔드 무관하게 같은 페어를 돌려받기 위함.
def _original_bytes(file_id: str) -> bytes:
    data = storage.load_original(file_id)
    if data is None:
        raise HTTPException(404, "원본 없음")
    return data


def _result_bytes(file_id: str, preset: str) -> bytes:
    data = storage.load("result", f"{file_id}_{preset}.jpg")
    if data is None:
        raise HTTPException(404, "결과 없음 (변환 1회 먼저)")
    return data


# ---- 세션 기반 (디스크 재료, 업로드 불필요) ----
@router.get("/originals")
def dev_originals():
    """이미 저장된 원본 목록."""
    return {"originals": sorted(
        Path(n).stem for n in storage.list_files("original") if n.endswith(".jpg"))}


@router.post("/verify")
def dev_verify(req: DevPairReq):
    """스테이지 3만 — 체크포인트 리플레이 (생성 비용 0)."""
    inspect = storage.load("quality", f"{req.file_id}_{req.preset}_inspect.json")
    if inspect is None:
        raise HTTPException(404, "체크포인트 없음 (변환 1회 먼저)")
    ins = json.loads(inspect.decode("utf-8"))
    # 파이프라인 게이트와 같은 대상(마크 + 읽은 글자) · 같은 물건 이름 · 깨진 응답은 실패로 (없던 글자 검사는 빠진다)
    targets = pipeline._verify_targets(ins)
    try:
        checks = detector.verify_and_locate(
            _result_bytes(req.file_id, req.preset), targets, ins.get("item", "object"), strict=True)   # ⭐ 저장본 계약
    except ValueError as e:
        raise HTTPException(502, f"verify 응답이 깨짐: {e}")
    return {"anchors": targets, "checks": checks,
            "gate_passed": detector.all_preserved(checks, expected=len(targets))}


@router.post("/transform-with-result")
async def dev_transform_with_result(
    file_id: str = Form(...),
    preset: str = Form(...),
    result: UploadFile = File(...),
):
    """generate() 를 건너뛰고, 업로드한 이미지를 결과로 삼아 이후 단계
    (verify/judge)만 돌린다 — fal.ai 를 매번 기다리지 않고 반복 테스트하기 위함."""
    data = await result.read()
    try:
        out = pipeline.run_transform_with_result(file_id, preset, data)
    except FileNotFoundError:
        raise HTTPException(404, "원본 없음")
    return {"result_url": storage.result_url(file_id, preset), **out}


@router.post("/auto-feedback")
def dev_auto_feedback(req: DevPairReq):
    """실사용자 피드백 대신 VLM이 결과를 보고 별점+코멘트를 남겨 저장한다
    (source="agent" — 실사용자 피드백이 이미 있으면 덮어쓰지 않음)."""
    out = auto_feedback.generate_feedback(
        _original_bytes(req.file_id), _result_bytes(req.file_id, req.preset))
    store.save_feedback(req.file_id, req.preset, out["rating"], out["comment"],
                         source="agent")
    return store.get_feedbacks(req.file_id, req.preset)["agent"]


# ---- 인박스 기반 (사용자가 storage/inbox/ 에 던져둔 새 원본 + 이미지 URL) ----
@router.post("/run-inbox")
async def dev_run_inbox(req: DevRunInboxReq = DevRunInboxReq()):
    """storage/inbox/ 의 원본 파일들 + 직접 준 이미지 URL들을 실제 파이프라인
    (fal.ai 생성 포함)으로 돌리고, 바로 자동 피드백 에이전트까지 붙여 DB에 남긴다.
    (다운로드/저장/파이프라인/피드백 저장 자체는 ingest.ingest_and_feedback 에 위임 —
    이 라우터는 "인박스 파일이냐 URL이냐, 어떤 입력을 줄지"만 고른다.)

    한 장 실패해도 나머지는 계속 처리 — 성공한 로컬 원본만 inbox/done/ 으로
    (file_id를 붙여) 옮겨서 다음 실행 때 중복 처리와 파일명 충돌을 막고,
    실패분은 inbox에 그대로 남아 재시도 가능. 동시 호출은 409로 거부 —
    처리 중 fal.ai/VLM 실호출이 겹치면 같은 사진이 두 번 돌아 비용이 두 배가 된다."""
    try:
        get_preset(req.preset)
    except KeyError:
        raise HTTPException(400, f"알 수 없는 프리셋: {req.preset}")

    if not _inbox_lock.acquire(blocking=False):
        raise HTTPException(409, "이미 실행 중 — 완료 후 다시 시도")
    try:
        inbox = storage.BASE / "inbox"
        done = inbox / "done"
        inbox.mkdir(parents=True, exist_ok=True)
        done.mkdir(parents=True, exist_ok=True)

        rows = []

        for path in sorted(inbox.iterdir()):
            if not path.is_file() or path.suffix.lower() not in INBOX_EXTS:
                continue
            file_id = uuid.uuid4().hex
            try:
                row = await run_in_threadpool(
                    ingest.ingest_and_feedback,
                    file_id, path.read_bytes(), path.suffix.lower(), req.preset,
                    source="inbox", original_name=path.name)
            except Exception as e:
                rows.append({"source_file": path.name, "file_id": file_id, "error": str(e)})
                continue
            try:
                path.rename(done / f"{file_id}_{path.name}")
            except Exception as e:
                row["move_error"] = str(e)
            rows.append({"source_file": path.name, "file_id": file_id, **row})

        for url in req.urls:
            file_id = uuid.uuid4().hex
            try:
                data, ext = await fetch_image(url)
                row = await run_in_threadpool(
                    ingest.ingest_and_feedback, file_id, data, f".{ext}", req.preset,
                    source="inbox_url", original_name=url)
            except HTTPException as e:
                rows.append({"source_url": url, "file_id": file_id, "error": e.detail})
                continue
            except Exception as e:
                rows.append({"source_url": url, "file_id": file_id, "error": str(e)})
                continue
            rows.append({"source_url": url, "file_id": file_id, **row})

        return {"rows": rows}
    finally:
        _inbox_lock.release()


@router.get("/gallery")
def dev_gallery():
    """저장된 원본 갤러리 (URL 조립만, 서빙은 /storage 라우트가 storage 를 거쳐)."""
    originals = [{"name": Path(n).stem, "url": f"/storage/original/{n}"}
                 for n in sorted(storage.list_files("original")) if n.endswith(".jpg")]
    return {"originals": originals}


FILE_ID_RE = re.compile(r"[0-9a-f]{32}")
logger = logging.getLogger("carret.dev")


def _load_json(kind: str, name: str):
    data = storage.load(kind, name)
    return json.loads(data.decode("utf-8")) if data is not None else None


def _safe(fn, *args):
    """항목 하나의 산출물이 깨져 있어도(쓰다 만 json, DB 락 등) 목록 전체를
    500 으로 날리지 않는다 — 그 칸만 None 으로 비우고 로그만 남긴다."""
    try:
        return fn(*args)
    except Exception:
        logger.exception(f"[dev/results] {getattr(fn, '__name__', fn)}{args} 실패(무시)")
        return None


def _prefix_map(names, sep: str) -> dict:
    """파일 이름 목록을 한 번만 훑어 {file_id: 이름} — 결과마다 찾지 않기 위함."""
    out = {}
    for n in sorted(names):
        fid = n[:32]
        if FILE_ID_RE.fullmatch(fid) and n[32:33] == sep:
            out.setdefault(fid, n)
    return out


def _local_names(folder: Path) -> list[str]:
    """inbox 는 사용자가 파일을 던져 두는 로컬 폴더 (storage 백엔드와 무관)."""
    return [f.name for f in folder.iterdir() if f.is_file()] if folder.is_dir() else []


@router.get("/results")
def dev_results():
    """파이프라인이 만든 결과 전부를 원본과 묶어서 한 번에 — 판정(judge),
    인스펙트(anchors/checks/gate/guard), 피드백, 원본 이름을 모아 돌려준다.
    계산은 하지 않는다: 이미 저장된 산출물을 읽기만 한다 (최신순).
    목록은 storage 를 거친다 (STORAGE_BACKEND=local/s3 무관), 이미지 URL 은 /storage 라우트와 짝."""
    originals = _prefix_map(storage.list_files("original"), ".")
    done = _prefix_map(_local_names(storage.BASE / "inbox" / "done"), "_")
    found = []
    for n, mtime in storage.list_files("result").items():
        if not n.endswith(".jpg"):
            continue
        file_id, _, preset = Path(n).stem.partition("_")
        if not FILE_ID_RE.fullmatch(file_id) or not preset:
            continue
        found.append((mtime, n, file_id, preset))
    found.sort(key=lambda t: t[0], reverse=True)

    items = []
    for mtime, n, file_id, preset in found:
        orig = originals.get(file_id)
        meta = _safe(store.get_original, file_id) or {}
        # 원본 파일명: DB 에 없으면(메타 기록 도입 전 실행분) inbox/done/{file_id}_{원래이름} 에서 복원
        name = meta.get("original_name")
        if not name and file_id in done:
            name = done[file_id][33:]
        items.append({
            "file_id": file_id,
            "preset": preset,
            "orig": f"/storage/original/{orig}" if orig else None,
            "result": f"/storage/result/{n}",
            "created": mtime,
            "name": name,
            "db": _safe(store.get_result, file_id, preset),
            "judge": _safe(_load_json, "quality", f"{file_id}_{preset}.json"),
            "inspect": _safe(_load_json, "quality", f"{file_id}_{preset}_inspect.json"),
            "feedback": _safe(store.get_feedbacks, file_id, preset) or {"user": None, "agent": None},
        })
    return {"items": items}
