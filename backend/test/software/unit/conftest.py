"""유닛 테스트 공통 안전망 (네트워크·비용 0 유지).

- no_real_vlm: .env 에 실제 VLM_KEY 가 있어도 detector/judge/auto_feedback 이 진짜
  Gemini 클라이언트를 만들지 못하게 한다. 가짜 클라이언트가 필요한 테스트는
  각자 `monkeypatch.setattr(<module>, "get_client", ...)` 로 덮어쓴다 (이 픽스처 뒤라 우선).
  validate_result 가 check_photo 를 가드와 동시에(백그라운드) 시작하므로, check_photo 를
  안 바꾼 테스트도 이제 실제 호출을 낼 수 있다 — 그 구멍을 막는다.
- drain_pipeline_background: 가드가 막은 시도의 check_photo 는 아무도 기다리지 않는다.
  테스트가 끝나 monkeypatch 가 원복된 뒤에 그 스레드가 진짜 함수를 부르지 않게,
  원복 전에(이 픽스처가 monkeypatch 에 의존하므로 먼저 정리된다) 전부 끝나길 기다린다.
"""
from concurrent.futures import wait

import pytest


@pytest.fixture(autouse=True)
def no_real_vlm(monkeypatch):
    from app.services.ai import auto_feedback, detector, judge

    def _blocked():
        raise RuntimeError("유닛 테스트에서 실제 VLM 클라이언트 금지")
    for mod in (detector, judge, auto_feedback):
        monkeypatch.setattr(mod, "get_client", _blocked)


@pytest.fixture(autouse=True)
def drain_pipeline_background(monkeypatch):
    from app.services import pipeline

    futures = []
    real = pipeline._in_background

    def tracking(fn, *args, **kw):
        f = real(fn, *args, **kw)
        futures.append(f)
        return f
    monkeypatch.setattr(pipeline, "_in_background", tracking)
    yield futures
    _, not_done = wait(futures, timeout=10)
    assert not not_done, "파이프라인 백그라운드 작업이 테스트 뒤에도 끝나지 않음"
