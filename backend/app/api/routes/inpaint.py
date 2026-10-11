"""칠한 곳 고치기 A/B (10-11) — 화면: frontend/inpaint.html.
POST /api/inpaint       칠한 마스크 + 설명 → 두 방식(flash · kontext)을 같이 돌려 a · b 로 (어느 쪽인지 숨김)
POST /api/inpaint/pick  판매자가 고른 쪽 기록 → 그때 모델을 밝힌다. 고른 사진이 다음 칠하기의 바탕
기록: DB inpaint_rounds (피드백의 한 종류) · 마스크 그림은 quality/{file_id}_ip{round}_mask.png"""
import base64
import io
import logging
import random
import re
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from PIL import Image
from pydantic import BaseModel, Field

from app.services.ai import inpainter
from app.services.persistence import storage, store

router = APIRouter()
logger = logging.getLogger("carret.inpaint")

KEY = r"^[A-Za-z0-9_-]{1,120}$"
ROUND_SUFFIX = re.compile(r"(-ip[0-9a-f]{8}[ab])+$")   # 이어 고쳐도 키가 길어지지 않게 — 마지막 회차만 (10-11 리뷰)
_running: set[str] = set()                            # 같은 사진에 A·B 가 동시에 두 번 돌지 않게 (유료 2회씩)
_lock = threading.Lock()
MAX_MASK = 8 * 1024 * 1024


class InpaintRequest(BaseModel):
    file_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    preset: str = Field(pattern=KEY, description="고칠 결과 사진의 키 (transform 응답 preset) — 고친 결과의 키도 된다")
    mask: str = Field(description="칠한 곳 PNG (data URL, 칠한 곳이 불투명)")
    note: str = Field(min_length=1, max_length=200, description="칠한 곳을 어떻게 고칠지")


class InpaintPick(BaseModel):
    file_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    round_id: str = Field(pattern=r"^[a-f0-9]{8}$")
    pick: Literal["a", "b", "none"]


def _mask_bytes(data_url: str) -> bytes:
    if not data_url.startswith("data:image/png;base64,"):
        raise HTTPException(422, "마스크는 PNG 여야 해요")
    try:
        raw = base64.b64decode(data_url.split(",", 1)[-1], validate=True)
    except ValueError:
        raise HTTPException(422, "마스크 형식이 잘못됐어요")
    if len(raw) > MAX_MASK:
        raise HTTPException(413, "마스크가 너무 커요")
    return raw


@router.post("/inpaint")
def inpaint(req: InpaintRequest):
    base = storage.load("result", f"{req.file_id}_{req.preset}.jpg")
    original = storage.load_original(req.file_id)
    if base is None or original is None:
        raise HTTPException(404, "고칠 사진을 찾을 수 없어요")
    try:
        size = Image.open(io.BytesIO(base)).size
        mask = inpainter.mask_image(_mask_bytes(req.mask), size)
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(422, "마스크를 읽을 수 없어요")
    if not mask.getbbox():
        raise HTTPException(422, "고칠 곳을 칠해 주세요")

    with _lock:
        if req.file_id in _running:
            raise HTTPException(409, "이 사진은 이미 고치는 중이에요")
        _running.add(req.file_id)
    try:
        return _run_round(req, base, original, mask, size)
    finally:
        with _lock:
            _running.discard(req.file_id)


def _run_round(req: InpaintRequest, base: bytes, original: bytes, mask, size) -> dict:
    rid = secrets.token_hex(4)
    root = ROUND_SUFFIX.sub("", req.preset)
    order = list(inpainter.MODELS)
    random.shuffle(order)                     # 블라인드 — 화면은 a · b 만 안다
    slots = dict(zip("ab", order))
    t0 = time.time()
    with ThreadPoolExecutor(2) as ex:
        futs = {s: ex.submit(inpainter.run, m, base, original, mask, req.note) for s, m in slots.items()}
    out, errors = {}, {}
    for s, f in futs.items():
        try:
            key = f"{root}-ip{rid}{s}"
            storage.save("result", f"{req.file_id}_{key}.jpg", f.result())
            out[s] = {"preset": key, "url": storage.result_url(req.file_id, key)}
        except Exception as e:
            logger.exception(f"[inpaint] {slots[s]} 실패")
            errors[s] = type(e).__name__
    if not out:
        raise HTTPException(502, "두 방식 모두 실패했어요")

    # 결과는 이미 나왔다(비용 발생) — 기록 실패로 500 을 주면 다시 눌러 또 돈이 나간다 (10-11 리뷰)
    try:
        storage.save("quality", f"{req.file_id}_ip{rid}_mask.png", inpainter.png(mask))
        store.record_inpaint(rid, req.file_id, req.preset, req.note, slots,
                             {m: inpainter.prompt_for(m, req.note) for m in order},
                             round(mask.histogram()[255] / (size[0] * size[1]), 4), errors, round(time.time() - t0, 1))
    except Exception:
        logger.exception("[inpaint] 기록 실패 — 결과는 돌려준다")
    return {"round_id": rid, "results": out, "failed": sorted(errors)}


@router.post("/inpaint/pick")
def pick(req: InpaintPick):
    r = store.get_inpaint(req.file_id, req.round_id)
    if r is None:
        raise HTTPException(404, "기록을 찾을 수 없어요")
    if r.get("pick"):
        raise HTTPException(409, "이미 고른 회차예요")
    if req.pick in (r.get("errors") or {}):
        raise HTTPException(422, "실패한 쪽은 고를 수 없어요")
    store.pick_inpaint(req.round_id, req.pick, r["slots"].get(req.pick))
    root = ROUND_SUFFIX.sub("", r["base_key"])
    return {"slots": r["slots"], "prompts": r["prompts"],
            "next": f"{root}-ip{r['round_id']}{req.pick}" if req.pick != "none" else r["base_key"]}


@router.get("/inpaint/stats")
def stats(file_id: str | None = Query(default=None, pattern=r"^[a-f0-9]{32}$")):
    """어느 모델이 몇 번 골렸나 — file_id 없으면 전체."""
    return {m: 0 for m in inpainter.MODELS} | {"none": 0, "undecided": 0} | store.inpaint_stats(file_id)
