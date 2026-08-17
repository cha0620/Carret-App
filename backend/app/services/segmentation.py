"""배경 제거 모듈: 상품 본체만 남기고 배경을 투명하게 만든다."""
import io
from functools import lru_cache
from pathlib import Path

from PIL import Image
from rembg import new_session, remove


@lru_cache(maxsize=1)
def get_session():
    """모델은 한 번만 로드해서 재사용 (로드가 느려서)."""
    return new_session("u2net")


def remove_background(original_path: Path, cutout_path: Path, mask_path: Path) -> dict:
    input_bytes = original_path.read_bytes()

    # rembg 실행 → 투명 배경(알파 채널) PNG 바이트 반환
    output_bytes = remove(
        input_bytes,
        session=get_session(),
        post_process_mask=True,   # 마스크 경계 정리
    )
    cutout_path.parent.mkdir(parents=True, exist_ok=True)
    cutout_path.write_bytes(output_bytes)

    # 알파 채널에서 마스크 추출 (나중에 AI inpainting에 사용)
    cutout_img = Image.open(io.BytesIO(output_bytes)).convert("RGBA")
    mask_img = cutout_img.getchannel("A")
    mask_path.parent.mkdir(parents=True, exist_ok=True)
    mask_img.save(mask_path)

    return {"cutout": str(cutout_path), "mask": str(mask_path)}