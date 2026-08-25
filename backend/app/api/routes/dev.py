"""개발 도구 — 스테이지 단위 테스트 API (dev 전용)."""
import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.services import detector, storage
from app.util import evaluator

router = APIRouter()


# ---- 요청 스키마 ----
class DevDetectReq(BaseModel):
    file_id: str


class DevPairReq(BaseModel):
    file_id: str
    preset: str


class DevNameReq(BaseModel):
    name: str


# ---- 세션 기반 (디스크의 원본 활용, 업로드 불필요) ----
@router.get("/originals")
def dev_originals():
    """이미 저장된 원본 목록."""
    return {"originals": sorted(
        p.stem for p in (storage.BASE / "original").glob("*.jpg"))}


@router.post("/detect")
def dev_detect(req: DevDetectReq):
    """스테이지 1만: 원본 → anchors."""
    original = storage.original_of(req.file_id)
    if original is None:
        raise HTTPException(404, "원본 없음")
    return {"anchors": detector.detect_defects(original.read_bytes())}


@router.post("/verify")
def dev_verify(req: DevPairReq):
    """스테이지 3만: 체크포인트 리플레이 (생성 비용 0)."""
    B = storage.BASE
    result = B / "result" / f"{req.file_id}_{req.preset}.jpg"
    inspect = B / "quality" / f"{req.file_id}_{req.preset}_inspect.json"
    if not result.exists() or not inspect.exists():
        raise HTTPException(404, "체크포인트 없음 (변환 1회 먼저)")
    anchors = json.loads(inspect.read_text(encoding="utf-8"))["anchors"]
    checks = detector.verify_and_locate(result.read_bytes(), anchors)
    return {"anchors": anchors, "checks": checks,
            "bubbles": detector.bubbles(checks),
            "gate_passed": detector.all_preserved(checks)}


@router.post("/eval-pair")
def dev_eval_pair(req: DevPairReq):
    """업로드 세션 1쌍 평가 — 로직은 evaluator 에 위임."""
    original = storage.original_of(req.file_id)
    result = storage.BASE / "result" / f"{req.file_id}_{req.preset}.jpg"
    if original is None or not result.exists():
        raise HTTPException(404, "원본/결과 없음")
    return evaluator.eval_bytes(original.read_bytes(), result.read_bytes(),
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
    """storage 을 브라우저용 갤러리로 (URL 만 조립, 서빙은 마운트가)."""
    pairs = []
    for name in evaluator.find_pairs():
        pair = evaluator.pair_of(name)
        rel_o = pair[0].relative_to(storage.BASE)
        rel_a = pair[1].relative_to(storage.BASE)
        pairs.append({"name": name,
                      "orig": f"/storage/{rel_o.as_posix()}",
                      "after": f"/storage/{rel_a.as_posix()}"})

    originals = [{"name": p.stem, "url": f"/storage/original/{p.name}"}
                 for p in sorted((storage.BASE / "original").glob("*.jpg"))]
    return {"pairs": pairs, "originals": originals}