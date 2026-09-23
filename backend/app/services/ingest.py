"""새 원본 이미지 1장을 실제 파이프라인(생성 모델 포함) + 자동 피드백까지
한 번에 태우는 단일 진입점 — dev.py 의 인박스/URL 수집 양쪽에서 재사용
(dev.py 는 "어떤 입력을 줄지"만 고르고, 계산은 여기 + pipeline/auto_feedback 이 갖는다).

file_id 는 호출부가 미리 만들어서 넘긴다 — 그래야 도중에 실패해도 호출부가
"어떤 file_id 아래 원본이 저장됐는지" 알고 에러에 남길 수 있다."""
import logging

from app.services import pipeline
from app.services.ai import auto_feedback
from app.services.persistence import storage, store

logger = logging.getLogger("carret.ingest")


def ingest_and_feedback(file_id: str, data: bytes, ext: str, preset: str,
                         source: str = "inbox", original_name: str | None = None) -> dict:
    size_bytes = storage.save("original", f"{file_id}{ext}", data)
    try:
        store.record_original(file_id, ext, source, original_name=original_name,
                               size_bytes=size_bytes)
    except Exception:
        # 메타데이터 기록 실패로 이미 저장된 원본 + 뒤이은 실호출(fal.ai/VLM)까지
        # 통째로 날릴 이유는 없다 — pipeline.py의 detect/verify/judge와 같은 원칙.
        logger.exception("원본 메타데이터 기록 실패(무시)")
    pipeline.run_transform(file_id, preset)
    original = storage.load_original(file_id)
    result = storage.load("result", f"{file_id}_{preset}.jpg")
    out = auto_feedback.generate_feedback(original, result)
    store.save_feedback(file_id, preset, out["rating"], out["comment"], source="agent")
    return {"result_url": storage.result_url(file_id, preset),
            **store.get_feedback(file_id, preset)}
