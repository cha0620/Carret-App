"""변환 API - HTTP 껍데기.

하는 일 딱 3개:
1. 요청 검증 (스키마가 자동)
2. 파이프라인 호출
3. 에러 → 상태코드 번역
"""
import hashlib
import json
import logging
import threading
from functools import partial
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException
from fastapi import Path as Path_
from fastapi.responses import FileResponse, Response

from app.prompts.presets import NOTE_MAX_CHARS, PRESETS, PURPOSES, style_key
from app.schemas.image import (AnalyzeRequest, AnalyzeResponse, ComponentsRequest, StylesResponse,
                               TransformRequest, TransformResponse)
from app.services import pipeline, style_refs
from app.services.pipeline import progress
from app.services.persistence import storage


logger = logging.getLogger("carret")
router = APIRouter()


@router.get("/styles", response_model=StylesResponse)
def styles():
    """무드 카드·목적 — 프론트가 이 목록으로 그린다 (무드 정의는 presets.py 한 곳)."""
    return StylesResponse(
        moods=[{"key": k, "name": v["name"], "emoji": v["emoji"],
                "color": "#%02x%02x%02x" % v["bg_color"]} for k, v in PRESETS.items()],
        purposes=[{"key": k, **v} for k, v in PURPOSES.items()],
        note_max_chars=NOTE_MAX_CHARS)


@router.get("/refs")
def refs():
    """정답 구도 목록 (10-09) — 판매자가 고른다. 선 그림이 있는 것만 (원본 정답 사진은 생성에 넣지 않는다)."""
    out = []
    for e in style_refs.load():
        if style_refs.sketch_path(e["file"]) is not None:
            out.append({"file": e["file"], "note": e.get("note") or "", "category": e.get("category"),
                        "items": e.get("items") or [], "needs": e.get("needs") or [],
                        "sketch_url": f"/api/refs/{Path(e['file']).stem}.png"})
    return {"refs": out}


@router.get("/refs/{name}.png")
def ref_sketch(name: str):
    """선 그림 이미지 — 목록에 있는 이름만 (경로 탈출 막기)."""
    known = {Path(e["file"]).stem: e["file"] for e in style_refs.load()}
    p = style_refs.sketch_path(known[name]) if name in known else None
    if p is None:
        raise HTTPException(status_code=404, detail="선 그림이 없어요")
    return FileResponse(p, media_type="image/png")


def _extra_bytes(ids: list[str] | None) -> list[tuple[str, bytes]]:
    out = []
    for fid in ids or []:
        b = storage.load_original(fid)
        if b is not None:
            out.append((fid, b))
    return out


@router.post("/components")
def components(req: ComponentsRequest):
    """게시글 구성품 (10-09) — 화면이 전체 분석(/api/analyze)과 동시에 부른다. 기준 + 같이 넣는 참고 사진을 같이
    보고, 세트는 부품까지 쪼갠다. 안의 구성품이 하나도 안 보이면 contents_hidden."""
    original = storage.load_original(req.file_id)
    if original is None:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    extras = _checked_extra_views(req)
    try:
        got = pipeline.components_for(req.file_id, original, _extra_bytes(extras))
    except Exception:
        logger.warning("구성품 빠른 분석 실패 — 전체 분석을 기다림", exc_info=True)
        raise HTTPException(status_code=503, detail="구성품을 빨리 보지 못했어요 — 전체 분석을 기다려요")
    return {"objects": [{"index": i, **o} for i, o in enumerate(got["objects"])],
            "contents_hidden": got.get("contents_hidden", False), "extras": got.get("extras", [])}


@router.post("/analyze", response_model=AnalyzeResponse)
def analyze(req: AnalyzeRequest):
    """업로드 직후 미리 분석 — 무드를 고르기 전에 "이 사진은 어떻게 처리되는지" 보여준다.
    결과는 저장돼 변환 때 다시 쓴다 (VLM 을 두 번 부르지 않는다)."""
    original = storage.load_original(req.file_id)
    if original is None:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    out = pipeline.load_analysis(req.file_id) or pipeline.analyze_original(req.file_id, original)
    if out.get("detect_failed"):
        raise HTTPException(status_code=503, detail="분석하지 못했습니다 — 변환은 할 수 있어요")
    objects = [{"index": i, **o} for i, o in enumerate(out.get("objects") or [])]
    return AnalyzeResponse(item=out.get("item", "object"), item_count=out.get("item_count") or 1,
                           objects=objects, photo_type=out.get("photo_type"),
                           wear_level=out.get("wear_level"), text_level=out.get("text_level"))


def _checked_extra_views(req: TransformRequest) -> list[str] | None:
    """같이 넣을 사진 검증 (10-09 리뷰) — 화면만 믿지 않는다: 같은 묶음 · 같은 물건 · 상품 사진(근거 X)만,
    메인 자신 · 중복은 뺀다. 아니면 400."""
    if not req.extra_view_ids:
        return None
    from app.api.routes import items as items_route
    item = items_route._load(req.item_id)
    photos = {p["file_id"]: p for p in (item or {}).get("photos", [])}
    main = photos.get(req.file_id)
    ids = list(dict.fromkeys(i for i in req.extra_view_ids if i != req.file_id))
    ok = main is not None and main.get("object") and all(
        i in photos and photos[i].get("object") == main["object"] and photos[i].get("slot", "product") == "product"
        for i in ids)
    if not ok:
        raise HTTPException(status_code=400, detail="같이 넣을 사진은 같은 물건의 상품 사진이어야 해요")
    return ids or None


def _result_key(req: TransformRequest, extras: list[str] | None, ref: dict | None) -> str:
    """결과 이름 — 입력 조합(같이 넣은 사진 · 정답 · 고른 구성품 · 부가품)이 다르면 다른 이름 (10-09 리뷰: 덮어쓰기 방지)."""
    key = style_key(req.preset, req.note) + ({"generate": "-g", "composite": "-c"}.get(req.mode or "", ""))
    if req.separate:
        return key + f"-o{req.sell[0]}"     # 물건마다 따로 — 같은 사진의 다른 물건 결과를 덮어쓰지 않게 (10-05)
    combo = {"v": sorted(extras or []), "r": ref["file"] if ref else None,
             "s": sorted(req.sell or []), "a": sorted(req.accessories or []), "m": sorted(req.mains or []),
             "q": req.arrange}
    if any(combo.values()):
        key += "-" + hashlib.sha1(json.dumps(combo, sort_keys=True).encode()).hexdigest()[:10]
    return key


@router.post("/transform", response_model=TransformResponse)
def transform(req: TransformRequest, background: BackgroundTasks):
    # def = 스레드풀 실행 (rembg 같은 블로킹 작업용 ✅)
    resp, signals = _transform(req)
    if signals:
        background.add_task(signals)
    return resp


def _transform(req: TransformRequest, job_id: str | None = None):
    """변환 본체 — 동기 POST /transform 과 작업(POST /transform/jobs)이 같이 쓴다.
    반환: (응답, 응답 뒤에 돌릴 관측 신호 — 인자 없는 호출 — 또는 None). job_id 는 파이프라인 상태로 넘겨 노드가 진행을 알린다.
    안에서 난 예외는 여기서 HTTPException 으로 바꾼다 (로그도 여기서)."""
    try:
        ref = next((e for e in style_refs.load() if e["file"] == req.ref_file), None) if req.ref_file else None
        if req.ref_file and (ref is None or style_refs.sketch_path(ref["file"]) is None):
            raise HTTPException(status_code=400, detail="고른 정답 구도를 찾을 수 없어요")
        extras = _checked_extra_views(req)
        # 화면이 본 구성품 목록이 다른 참고 사진 조합으로 만든 거면 지금 조합으로 다시 맞춘다. 그 경우 화면이 보낸
        # 번호(sell · accessories)는 옛 목록 번호라 버린다 — 조용히 다른 물건을 고르지 않게 (10-09 리뷰)
        sell, accessories, mains = req.sell, req.accessories, req.mains
        cached = pipeline.load_components(req.file_id)
        extra_b = _extra_bytes(extras)
        if isinstance(cached, dict) and cached.get("extras") != [f for f, _ in extra_b]:
            original = storage.load_original(req.file_id)
            if original is not None:
                pipeline.components_for(req.file_id, original, extra_b)
                if sell or accessories:
                    logger.warning("[transform] 구성품 목록이 바뀌어 고른 번호를 버림 — 분석 판단대로")
                sell, accessories, mains = None, None, None
        key = _result_key(req, extras, ref)
        out = pipeline.run_transform(req.file_id, key, defer_signals=True, note=req.note, arrange=req.arrange, job_id=job_id,
                                     sell=sell, mains=mains,
                                     extra_view_ids=extras, extra_views_explicit=bool(extras), user_mode=req.mode,
                                     clean_prompt="set",   # 앱 기본 (10-09): 세트면 종류 · 개수 JSON + 개수 게이트, 아니면 최소 문장
                                     # 정답 = 선 그림 + 구도 문장 (원본 정답 사진은 넣지 않는다 — 사용자 규칙)
                                     ref_file=ref["file"] if ref else None,
                                     ref_layout=(ref.get("layout") or None) if ref else None,
                                     ref_sketch=bool(ref), ref_keep=ref.get("keep") if ref else None)
    except HTTPException:
        raise
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="파일을 찾을 수 없습니다")
    except Exception:
        logger.exception("변환 실패")              # 서버 로그엔 상세히
        raise HTTPException(status_code=500, detail="변환 중 오류가 발생했습니다")

    # 관측 신호는 응답을 보낸 뒤 (사용자가 기다리지 않게) — 누끼 비교는 inspect 에.
    # judge 성적표는 운영에서 부르지 않는다 (10-05 — eval · dev 만)
    signals = None
    if out.get("item_signals_pending"):
        signals = partial(pipeline.item_signals_and_save, req.file_id, key, trace_id=out.get("trace_id"),
                          parent_span_id=out.get("trace_span_id"))

    resp = TransformResponse(
        file_id=req.file_id,
        preset=key,
        result_path=f"storage/result/{out['result_name']}",
        result_url=storage.result_url(req.file_id, key),
        prompt_used=out["prompt_used"],
        gate_passed=out["gate_passed"],
        accessories=accessories or [],              # 사진엔 안 넣고 같이 파는 것 — 생성은 sell 만 본다 (목록이 바뀌면 비움)
        item=out["item"],                           # ⭐
        considered=out["considered"],
        mode=out.get("mode", "generate"),
        detect_failed=out.get("detect_failed", False),
        verify_failed=out.get("verify_failed", False),
        composite_reason=out.get("composite_reason"),
        detail_pieces=out.get("detail_pieces") or [],
        detail_requests=out.get("detail_requests") or [],
        photo_type=out.get("photo_type"),
        wear_level=out.get("wear_level"),
        watermark=out.get("watermark"),
    )
    return resp, signals



# ── 작업으로 돌리기 (10-12 사용자: "결과 먼저 보여 주고 검사는 뒤에서") ──
# 시작하면 작업 id 를 바로 돌려주고, 화면은 GET 으로 물어 생성본을 "검사 중" 으로 먼저 보여 준다.
# 끝나면 result 가 동기 /transform 응답과 같은 모양. 미리보기는 메모리에만 (progress.py).

def _run_job(job_id: str, req: TransformRequest) -> None:
    try:
        resp, signals = _transform(req, job_id)
    except HTTPException as e:
        progress.finish(job_id, error={"status": e.status_code, "detail": e.detail})
        return
    except Exception:
        logger.exception("변환 작업 실패")
        progress.finish(job_id, error={"status": 500, "detail": "변환 중 오류가 발생했습니다"})
        return
    progress.finish(job_id, result=resp.model_dump())
    if signals:   # 결과를 알린 뒤 — 사용자가 기다리지 않게 (동기 경로의 background 와 같은 자리)
        try:
            signals()
        except Exception:
            logger.exception("관측 신호 실패(무시)")


@router.post("/transform/jobs")
def start_transform_job(req: TransformRequest):
    """같은 사진으로 이미 도는 작업이 있으면 그 id 를 돌려준다. 동시에 도는 작업이 상한이면 429."""
    try:
        job, new = progress.create(req.file_id)
    except progress.Busy:
        raise HTTPException(status_code=429, detail="지금 만드는 사진이 많아요 — 잠시 뒤 다시 눌러 주세요")
    if new:
        progress.publish(job.id, "preparing")
        threading.Thread(target=_run_job, args=(job.id, req), daemon=True).start()
    return {"job_id": job.id}


JOB_ID = r"^[a-f0-9]{32}$"


@router.get("/transform/jobs/{job_id}")
def transform_job(job_id: str = Path_(pattern=JOB_ID)):
    j = progress.get(job_id)
    if j is None:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없어요 (서버가 다시 켜졌을 수 있어요)")
    return {"job_id": j.id, "stage": j.stage, "attempt": j.attempt,
            "preview_url": f"/api/transform/jobs/{j.id}/preview?n={j.attempt}" if j.preview else None,
            "result": j.result, "error": j.error}


@router.get("/transform/jobs/{job_id}/preview")
def transform_job_preview(job_id: str = Path_(pattern=JOB_ID)):
    j = progress.get(job_id)
    if j is None or j.preview is None:
        raise HTTPException(status_code=404, detail="미리보기 없음")
    p = j.preview
    media = "image/png" if p[:8] == b"\x89PNG\r\n\x1a\n" else "image/webp" if p[8:12] == b"WEBP" else "image/jpeg"
    return Response(content=p, media_type=media, headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})
