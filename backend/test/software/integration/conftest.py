"""통합 테스트 안전망 — 실제 VLM 호출 금지 (unit/conftest.py 의 no_real_vlm 과 같은 규칙).

통합 테스트는 실제 라우트·그래프를 돌리고 VLM 함수만 개별로 모의한다. 모의가 빠진
함수(예: 앞단 함수 이름이 바뀌었는데 옛 이름만 모의)가 있으면
.env 의 실제 VLM_KEY 로 Gemini 를 부를 수 있었다 — 클라이언트 생성을 막아 모의가 빠진
호출이 비용 없이 예외(→ 파이프라인의 실패 경로)로 드러나게 한다.
"""
import pytest


@pytest.fixture(autouse=True)
def no_real_vlm(monkeypatch):
    from app.services.ai import auto_feedback, detector, judge

    def _blocked():
        raise RuntimeError("통합 테스트에서 실제 VLM 클라이언트 금지")
    for mod in (detector, judge, auto_feedback):
        monkeypatch.setattr(mod, "get_client", _blocked)
