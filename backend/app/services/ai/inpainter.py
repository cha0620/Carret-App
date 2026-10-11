"""칠한 곳 고치기 (10-11 사용자: "사진을 받고 특정 부분을 칠하고, 그 부분을 설명하면 다시 생성").
두 방식을 같이 돌려 판매자가 고른다 (A/B, 고르기 전엔 어느 쪽이 어느 모델인지 숨긴다):
  flash   — 지금 쓰는 생성 모델(fal_model). 마스크를 못 받아 마스크를 세 번째 사진으로 보여 주고 문장으로 가리킨다
  kontext — 마스크(mask_url) · 원본 참고(reference_image_url)를 받는 인페인팅 모델 (inpaint_b_model)
어느 쪽이든 결과는 칠한 영역만 원래 사진에 붙인다 — 칠하지 않은 곳은 픽셀까지 그대로."""
import io
import logging
import os

import fal_client
from PIL import Image, ImageFilter

from app.core.config import reveal, settings
from app.core.tracing import observe
from app.services.ai.generator import GEN_STEPS, _download, _upload

logger = logging.getLogger("carret.inpainter")

FEATHER = 4   # 붙이는 경계를 살짝 흐린다 (px) — 칼로 자른 선이 보이지 않게

# 고정 지시 "원본에 맞춰라" + 판매자 설명 (10-11 사용자). 원본은 참고 사진으로도 같이 준다
FLASH_PROMPT = ("In image 1, change only inside the white area of image 3: {note}. "
                "Match image 2, the original photo of the same item.")
KONTEXT_PROMPT = "{note}. Match the reference photo, the original of the same item."

MODELS = ("flash", "kontext")


def mask_image(mask_png: bytes, size: tuple[int, int]) -> Image.Image:
    """화면이 보낸 마스크(칠한 곳이 불투명) → 결과 크기의 흑백(흰색 = 고칠 곳)."""
    m = Image.open(io.BytesIO(mask_png), formats=["PNG"])
    if m.width > size[0] * 2 or m.height > size[1] * 2:   # 압축 폭탄 — 풀기 전에 거절 (10-11 리뷰)
        raise ValueError("mask too large")
    m = m.getchannel("A") if m.mode in ("RGBA", "LA") else m.convert("L")
    return m.resize(size).point(lambda v: 255 if v > 32 else 0)


def paste_back(base: bytes, edited: bytes, mask: Image.Image) -> bytes:
    """칠한 영역만 edited 에서 가져온다. 모델 출력 크기가 달라도 base 크기에 맞춘다."""
    b = Image.open(io.BytesIO(base)).convert("RGB")
    e = Image.open(io.BytesIO(edited)).convert("RGB").resize(b.size)
    m = mask.resize(b.size).filter(ImageFilter.GaussianBlur(FEATHER))
    out = io.BytesIO()
    Image.composite(e, b, m).save(out, "JPEG", quality=95)
    return out.getvalue()


def png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def prompt_for(model: str, note: str) -> str:
    return (FLASH_PROMPT if model == "flash" else KONTEXT_PROMPT).format(note=note.strip().rstrip("."))


def run(model: str, base: bytes, original: bytes, mask: Image.Image, note: str) -> bytes:
    """한 방식으로 고친 사진 (붙이기까지). 실패는 그대로 올린다 — 호출부가 한쪽 실패를 따로 보여 준다."""
    os.environ.setdefault("FAL_KEY", reveal(settings.fal_key))
    prompt = prompt_for(model, note)
    if model == "flash":
        endpoint = settings.fal_model
        args = {"image_urls": [_upload(base), _upload(original), _upload(png(mask))], "prompt": prompt}
        if "flash" in endpoint:
            args["num_inference_steps"] = GEN_STEPS
    else:
        endpoint = settings.inpaint_b_model
        args = {"image_url": _upload(base), "mask_url": _upload(png(mask)),
                "reference_image_url": _upload(original), "prompt": prompt}
    with observe("inpaint_" + model, as_type="generation", model=endpoint, input=prompt) as obs:
        result = fal_client.subscribe(endpoint, arguments=args)
        url = result["images"][0]["url"]
        if obs is not None:
            obs.update(output={"url": url})
    return paste_back(base, _download(url), mask)
