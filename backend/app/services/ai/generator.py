import logging
import os
import tempfile

import fal_client
import httpx

from app.core.config import reveal, settings
from app.core.tracing import observe

logger = logging.getLogger("carret.generator")


GEN_STEPS = 8   # flux-2 flash 는 8 이하 (eval/run.py 가 meta 에 남긴다)


MAX_IMAGES = 4   # flux-2 flash edit 는 앞의 4장만 쓴다


# FLUX.2 는 부정 프롬프트를 지원하지 않지만 qwen 은 따로 받는다 (10-09)
QWEN_NEGATIVE = ("added text, new labels, new tags, new logos, watermark, line drawing, sketch, "
                 "extra objects, extra views, collage, hanger, mannequin, person")


def _model_args(model: str) -> tuple[int, dict]:
    """모델별 (최대 입력 장수, 추가 인자) — 10-08 모델 비교. flash 는 8스텝, 정식 flux-2 는 기본(28),
    Nano Banana Pro 는 스텝 인자가 없고 14장까지 · 1K."""
    if "nano-banana" in model:
        return 14, {"resolution": "1K", "output_format": "jpeg", "num_images": 1}
    if "qwen-image" in model:
        # qwen-image-3/edit (10-09): 1~3장 · 순서를 "image 1, 2" 로 부른다 · 금지는 negative_prompt 로.
        # 프롬프트 확장(LLM 이 다시 씀)은 기본 켜짐 → 끈다 (물건 보존 문장이 바뀌면 안 됨)
        return 3, {"enable_prompt_expansion": False, "negative_prompt": QWEN_NEGATIVE,
                   "num_images": 1, "output_format": "jpeg"}
    if "flash" in model:
        return MAX_IMAGES, {"num_inference_steps": GEN_STEPS}
    return MAX_IMAGES, {}


def _generate_ai(image_bytes: bytes, preset: dict, seed: int | None = None,
                 style_ref: bytes | None = None, extra_views: list[bytes] | None = None) -> bytes:
    """생성 편집 모델: 프롬프트가 전체 편집을 지시 (마스크 불필요).
    seed=None 이면 fal 기본(랜덤) — 출력 가드 재시도만 seed 를 명시해서 바꾼다.
    style_ref 가 있으면 두 번째 이미지로 (스타일 참고 — 무엇을 따라 할지는 프롬프트가 정한다)."""
    os.environ.setdefault("FAL_KEY", reveal(settings.fal_key))

    # 순서: 주 사진 → 같은 물건 다른 각도 → 스타일 참고 (프롬프트가 이 순서로 가리킨다)
    max_images, model_kw = _model_args(settings.fal_model)
    extras = (extra_views or [])[:max_images - 1 - (1 if style_ref else 0)]
    image_urls = ([_upload(image_bytes)] + [_upload(b) for b in extras]
                  + ([_upload(style_ref)] if style_ref else []))

    arguments = {
        "image_urls": image_urls,            # ← 리스트로! (참조 이미지 목록, 첫 장이 원본)
        "prompt": preset["prompt"],
        **model_kw,
    }
    if preset.get("no_negative"):
        arguments.pop("negative_prompt", None)   # 최소 프롬프트 실험 (10-09)
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
            logger.warning(f"[generate] 결과 다운로드 실패 ({attempt}/{DOWNLOAD_ATTEMPTS}), 재시도: {e}")


def _upload(data: bytes) -> str:
    """fal 저장소에 올리고 주소를 받는다. 올리기 전에 flush — 안 하면 버퍼(8KB)보다 작은 파일
    (마스크 PNG 등)은 빈 파일로 올라간다 (09-29 인페인팅 실험에서 발견)."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(data)
        f.flush()
        return fal_client.upload_file(f.name)