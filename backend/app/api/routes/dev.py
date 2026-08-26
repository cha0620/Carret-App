"""개발 도구 — 스테이지 단위 테스트 API (dev 전용).

원칙: 이 모듈은 로직을 "소유하지 않는다".
모든 계산은 프로덕션 단일 진실원(detector/evaluator) 에 위임,
여기서는 "어떤 입력을 줄지" 만 선택한다.
= 테스트는 프로덕션을 호출하지, 복사하지 않는다.
"""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import detector, storage
from app.util import evaluator

router = APIRouter()


# ---- 요청 스키마 (이 라우터의 계약) ----
class DevDetectReq(BaseModel):
    file_id: str


class DevPairReq(BaseModel):
    file_id: str
    preset: str


class DevNameReq(BaseModel):
    name: str


# ---- 입력 해결사 (dev 유일의 소유 코드: 입력 선택 + 404) ----
def _original_bytes(file_id: str) -> bytes:
    original = storage.original_of(file_id)
    if original is None:
        raise HTTPException(404, "원본 없음")
    return original.read_bytes()


def _result_bytes(file_id: str, preset: str) -> bytes:
    path = storage.BASE / "result" / f"{file_id}_{preset}.jpg"
    if not path.exists():
        raise HTTPException(404, "결과 없음 (변환 1회 먼저)")
    return path.read_bytes()


# ---- 세션 기반 (디스크 재료, 업로드 불필요) ----
@router.get("/originals")
def dev_originals():
    """이미 저장된 원본 목록."""
    return {"originals": sorted(
        p.stem for p in (storage.BASE / "original").glob("*.jpg"))}


@router.post("/detect")
def dev_detect(req: DevDetectReq):
    """스테이지 1만 — 파이프라인과 "같은 함수"."""
    return {"anchors": detector.detect_defects(_original_bytes(req.file_id))}


@router.post("/verify")
def dev_verify(req: DevPairReq):
    """스테이지 3만 — 체크포인트 리플레이 (생성 비용 0)."""
    inspect = storage.BASE / "quality" / f"{req.file_id}_{req.preset}_inspect.json"
    if not inspect.exists():
        raise HTTPException(404, "체크포인트 없음 (변환 1회 먼저)")
    anchors = json.loads(inspect.read_text(encoding="utf-8"))["anchors"]
    checks = detector.verify_and_locate(
        _result_bytes(req.file_id, req.preset), anchors)   # ⭐ 저장본 계약
    return {"anchors": anchors, "checks": checks,
            "bubbles": detector.bubbles(checks),
            "gate_passed": detector.all_preserved(checks)}


@router.post("/eval-pair")
def dev_eval_pair(req: DevPairReq):
    """업로드 세션 1쌍 — evaluator 에 완전 위임."""
    return evaluator.eval_bytes(
        _original_bytes(req.file_id),
        _result_bytes(req.file_id, req.preset),
        name=req.file_id)


# ---- 데이터셋 기반 (평가셋 페어) ----
@router.get("/pairs")
def dev_pairs():
    return {"pairs": evaluator.find_pairs()}


@router.post("/eval-anchor")
def dev_eval_anchor(req: DevNameReq):
    row = evaluator.eval_pair(req.name)
    if row is None:
        raise HTTPException(404, "페어 없음")
    return row


@router.get("/eval-all")
def dev_eval_all():
    return {"rows": evaluator.eval_all()}


@router.get("/gallery")
def dev_gallery():
    """storage 을 브라우저 갤러리로 (URL 조립만, 서빙은 마운트가)."""
    pairs = []
    for name in evaluator.find_pairs():
        img, after = evaluator.pair_of(name)
        pairs.append({
            "name": name,
            "orig": f"/storage/{img.relative_to(storage.BASE).as_posix()}",
            "after": f"/storage/{after.relative_to(storage.BASE).as_posix()}",
        })
    originals = [{"name": p.stem, "url": f"/storage/original/{p.name}"}
                 for p in sorted((storage.BASE / "original").glob("*.jpg"))]
    return {"pairs": pairs, "originals": originals}