"""software 테스트(unit·integration·full) 공통 안전망 — 무거운 로컬 모델 금지.

DINOv2(embedder, 수백 MB·첫 로드 수 초)와 EasyOCR(local_ocr, 따로 설치·모델 다운로드)를
테스트가 실수로 띄우지 않게 _load 를 바로 실패하게 바꾼다. 가드 쪽(dino_band·item_dino·
item_patch·ocr_local)은 모델 실패를 삼키도록 설계돼 있어, 모델이 필요 없는 테스트는 그대로
통과하고 모델을 실제로 부르던 테스트는 값이 None 으로 바뀔 뿐이다.
모델 동작을 흉내 내는 테스트는 각자 _load(또는 embed/_patch_tokens)를 다시 바꿔 끼운다
(이 픽스처 뒤라 우선). eval 테스트(test/eval)는 이 디렉터리 밖이라 영향 없음.
"""
import pytest


@pytest.fixture(autouse=True)
def no_real_embedding_or_ocr_model(monkeypatch):
    from app.services.ai import embedder, local_ocr

    def _blocked():
        raise RuntimeError("테스트에서 실제 DINO/EasyOCR 모델 로드 금지")
    monkeypatch.setattr(embedder, "_load", _blocked)
    monkeypatch.setattr(local_ocr, "_load", _blocked)
