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
        image = _download(out_url)
        if obs is not None:
            obs.update(output={"url": out_url})
        return image


DOWNLOAD_ATTEMPTS = 2


def _download(url: str) -> bytes:
    """생성 결과 받기. 상태 코드를 확인한다 — 2026-09-26 fal CDN 이 500 HTML 페이지를 돌려줬는데
    그걸 이미지로 넘겨 뒷단(PIL)이 UnidentifiedImageError 로 터졌다. 5xx·연결 오류는 1회 재시도."""
    for attempt in range(1, DOWNLOAD_ATTEMPTS + 1):
        try:
            r = httpx.get(url, timeout=60, follow_redirects=True)   # CDN 리다이렉트
            r.raise_for_status()
            return r.content
        except (httpx.HTTPStatusError, httpx.TransportError) as e:
            status = getattr(getattr(e, "response", None), "status_code", None)
            if attempt == DOWNLOAD_ATTEMPTS or (status is not None and status < 500):
                raise
            print(f"[generate] 결과 다운로드 실패 ({attempt}/{DOWNLOAD_ATTEMPTS}), 재시도: {e}")


def _upload(data: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(data)
        return fal_client.upload_file(f.name)