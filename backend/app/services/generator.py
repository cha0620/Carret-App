import os
import tempfile

import fal_client
import httpx

from app.core.config import settings


def _generate_ai(image_bytes: bytes, preset: dict) -> bytes:
    """생성 편집 모델: 프롬프트가 전체 편집을 지시 (마스크 불필요)."""
    os.environ.setdefault("FAL_KEY", settings.fal_key)

    image_url = _upload(image_bytes)

    result = fal_client.subscribe(
    settings.fal_model,                      # fal-ai/flux-2
    arguments={
        "image_urls": [image_url],           # ← 리스트로! (참조 이미지 목록)
        "prompt": preset["prompt"],
        # num_inference_steps: 삭제 (기본값) 또는 8 이하
        "num_inference_steps": 8,
    },
)

    out_url = result["images"][0]["url"]
    return httpx.get(out_url, timeout=60).content


def _upload(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(data)
        return fal_client.upload_file(f.name)