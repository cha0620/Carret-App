"""변환 파이프라인 - 스테이지 조립.

HTTP도 모르고, 경로도 모른다 (저장은 storage에게만 부탁).
원칙: 일단 단순하게, 이상해지면 그때 리팩토링.
"""
from app.prompts.presets import get_preset
from app.services import storage
from app.core.config import settings

def run_transform(file_id: str, preset_key: str) -> dict:
    """원본 → 누끼 → 결과. 결과 정보 반환."""

    # 0) 원본 찾기 (없으면 호출측이 404로 변환)
    original = storage.original_of(file_id)
    if original is None:
        raise FileNotFoundError(file_id)
    preset = get_preset(preset_key)

    if settings.pipeline_mode == "pass":
        result_name = f"{file_id}_{preset_key}.jpg"
        storage.save("result", result_name, original.read_bytes())
        return {"result_name": result_name, "prompt_used": "PASS-THROUGH"}

    # 1) 스테이지 1: 배경 제거 (bytes → bytes)
    # cutout_bytes, mask_bytes = segmentation.remove_background(
    #     original.read_bytes()
    # )
    # storage.save("cutout", f"{file_id}.png", cutout_bytes)
    # storage.save("mask", f"{file_id}.png", mask_bytes)

    # # 2) 스테이지 2: 생성 (v1 합성 / v2 AI 공장 자동 선택)
    # result_name = f"{file_id}_{preset_key}.jpg"
    # storage.save(
    #     "result",
    #     result_name,
    #     generator.generate(cutout_bytes, mask_bytes, preset),
    # )

    return {
        "result_name": result_name,
        "prompt_used": preset["prompt"],
    }