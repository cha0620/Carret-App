"""새 원본 이미지 1장을 실제 파이프라인(생성 모델 포함) + 자동 피드백까지
한 번에 태우는 단일 진입점 — dev.py 의 인박스/URL 수집 양쪽에서 재사용
(dev.py 는 "어떤 입력을 줄지"만 고르고, 계산은 여기 + pipeline/auto_feedback 이 갖는다).

file_id 는 호출부가 미리 만들어서 넘긴다 — 그래야 도중에 실패해도 호출부가
"어떤 file_id 아래 원본이 저장됐는지" 알고 에러에 남길 수 있다."""
from app.services import auto_feedback, pipeline, storage, store


def ingest_and_feedback(file_id: str, data: bytes, ext: str, preset: str) -> dict:
    storage.save("original", f"{file_id}{ext}", data)
    pipeline.run_transform(file_id, preset)
    original = storage.load_original(file_id)
    result = storage.load("result", f"{file_id}_{preset}.jpg")
    out = auto_feedback.generate_feedback(original, result)
    store.save_feedback(file_id, preset, out["rating"], out["comment"], source="agent")
    return {"result_url": storage.result_url(file_id, preset),
            **store.get_feedback(file_id, preset)}
