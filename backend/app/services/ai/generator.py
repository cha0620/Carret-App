import os
import tempfile

import fal_client
import httpx

from app.core.config import settings
from app.core.tracing import observe


def _generate_ai(image_bytes: bytes, preset: dict, seed: int | None = None) -> bytes:
    """생성 편집 모델: 프롬프트가 전체 편집을 지시 (마스크 불필요).
    seed=None 이면 fal 기본(랜덤) — 출력 가드 재시도만 seed 를 명시해서 바꾼다."""
    os.environ.setdefault("FAL_KEY", settings.fal_key)

    image_url = _upload(image_bytes)

    arguments = {
        "image_urls": [image_url],           # ← 리스트로! (참조 이미지 목록)
        "prompt": preset["prompt"],
        # num_inference_steps: 삭제 (기본값) 또는 8 이하
        "num_inference_steps": 8,
    }
    if seed is not None:
        arguments["seed"] = seed

    with observe("generate_image", as_type="generation", model=settings.fal_model,
                 input=preset["prompt"]) as obs:
        result = fal_client.subscribe(
            settings.fal_model,                      # fal-ai/flux-2
            arguments=arguments,
        )

        out_url = result["images"][0]["url"]
        image = httpx.get(out_url, timeout=60).content
        if obs is not None:
            obs.update(output={"url": out_url})
        return image


def _upload(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(data)
        return fal_client.upload_file(f.name)