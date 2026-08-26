"""변환 파이프라인 - 스테이지 조립.

HTTP도 모르고, 경로도 모른다 (저장은 storage에게만 부탁).
원칙: 일단 단순하게, 이상해지면 그때 리팩토링.

스테이지:
  0) 원본 로드
  1) detect  : 원본 → 하자 의미 앵커 (what/where)
  2) generate: 배경 교체
  3) verify  : 결과 → 보존 여부 + 결과 좌표 (말풍선용)
  4) judge   : 품질 성적표 (캐시)
"""
import json

from app.core.config import settings
from app.prompts.presets import get_preset
from app.services import detector, judge, storage
from app.services.generator import _generate_ai

import logging, time
logger = logging.getLogger("carret.pipeline")


def run_transform(file_id: str, preset_key: str) -> dict:
    t0 = time.time()
    # 0) 원본 찾기 (없으면 호출측이 404로 변환)
    original = storage.original_of(file_id)
    if original is None:
        raise FileNotFoundError(file_id)
    original_bytes = original.read_bytes()      # ⭐ 한 번만 읽기
    preset = get_preset(preset_key)

    if settings.pipeline_mode == "pass":
        result_name = f"{file_id}_{preset_key}.jpg"
        storage.save("result", result_name, original_bytes)
        return {"result_name": result_name, "prompt_used": "PASS-THROUGH",
                "checks": [], "bubbles": [], "gate_passed": None}

    # 1) 스테이지 1: 검출 (원본 → 의미 앵커)
    try:
        anchors = detector.detect_defects(original_bytes)
    except Exception as e:
        print(f"[detect] 실패(무시): {e}")
        anchors = []

    # 2) 스테이지 2: 생성
    gen = _generate_ai(original_bytes, preset)
    result_name = f"{file_id}_{preset_key}.jpg"
    storage.save("result", result_name, gen)
    saved_bytes = (storage.BASE / "result" / result_name).read_bytes()
    # 3) 스테이지 3: 검증 (결과 → 보존 여부 + 좌표)
    checks, gate_passed = [], None
    if anchors:
        try:
            checks = detector.verify_and_locate(saved_bytes, anchors)
            gate_passed = detector.all_preserved(checks)
        except Exception as e:
            print(f"[verify] 실패(무시): {e}")

    # 디버그용 인스펙트 리포트 (말풍선/게이트 흔적 남기기)
    storage.save(
        "quality",
        f"{file_id}_{preset_key}_inspect.json",
        json.dumps({"anchors": anchors, "checks": checks,
                    "gate_passed": gate_passed},
                   ensure_ascii=False, indent=2).encode("utf-8"),
    )

    # 4) 스테이지 4: 저지 성적표 (캐시)
    quality_path = storage.BASE / "quality" / f"{file_id}_{preset_key}.json"
    if not quality_path.exists():
        try:
            report = judge.judge(original_bytes, gen)
        except Exception as e:
            print(f"[judge] 실패(무시): {e}")
            report = None
        if report:
            storage.save(
                "quality",
                f"{file_id}_{preset_key}.json",
                json.dumps(report, ensure_ascii=False, indent=2).encode("utf-8"),
            )
    logger.info(f"[detect] anchors={len(anchors)}")
    logger.info(f"[verify] preserved="
                f"{sum(c['preserved'] for c in checks)}/{len(checks)}")

    bubbles = detector.bubbles(checks)
    logger.info(f"transform {file_id}/{preset_key} "
                f"bubbles={bubbles} gate={gate_passed} "
                f"{time.time()-t0:.1f}s")
    return {
        "result_name": result_name,
        "prompt_used": preset["prompt"],
        "checks": checks,
        "bubbles": bubbles,   # 말풍선용 (보존+좌표만)
        "gate_passed": gate_passed,
    }