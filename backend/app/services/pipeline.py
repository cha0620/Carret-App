"""변환 파이프라인 - 스테이지 조립.

HTTP도 모르고, 경로도 모른다 (저장은 storage에게만 부탁).
원칙: 일단 단순하게, 이상해지면 그때 리팩토링.
"""
from app.prompts.presets import get_preset
from app.services import storage
from app.core.config import settings
from app.services.generator import _generate_ai
import json
from app.services import judge

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

    gen = _generate_ai(original.read_bytes(), preset)
    # 2) 스테이지 2: 생성 (v1 합성 / v2 AI 공장 자동 선택)
    result_name = f"{file_id}_{preset_key}.jpg"
    storage.save(
        "result",
        result_name,
        gen,
    )
    quality_path = storage.BASE / "quality" / f"{file_id}_{preset_key}.json"
    if not quality_path.exists():
        try:
            report = judge.judge(original.read_bytes(), gen)
        except Exception as e:
            print(f"[judge] 실패(무시): {e}")
            report = None
        if report:
            storage.save(
                "quality",
                f"{file_id}_{preset_key}.json",
                json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
            )

    return {
        "result_name": result_name,
        "prompt_used": preset["prompt"],
    }