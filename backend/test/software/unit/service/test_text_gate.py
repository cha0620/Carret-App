"""10-11 사용자: 글자는 재생성 · 배경 교체 판단에서 빼고 상세(검증) 사진으로 보강 — text_gate=false (기본)."""
import app.services.pipeline as pipeline_mod
from app.core.config import settings


def test_text_gate_off_dense_text_still_generates_and_targets_keep_logos_only(monkeypatch):
    monkeypatch.setattr(settings, "text_gate", False)
    s = {"text_level": "dense", "item_texts": [{"text": "ULTEGRA"}],
         "anchors": [{"category": "print", "what": "MERIDA logo"}, {"category": "print", "what": "ULTEGRA text"}]}
    assert [a["what"] for a in pipeline_mod._verify_targets(s)] == ["MERIDA logo"]
    assert not pipeline_mod._needs_text({"text_level": "simple"})
    assert pipeline_mod.detail_requests(s) and not pipeline_mod.detail_requests({"text_level": "none", "anchors": []})


def test_text_gate_on_dense_text_asks_no_detail_photos(monkeypatch):
    monkeypatch.setattr(settings, "text_gate", True)
    assert pipeline_mod.detail_requests({"text_level": "dense"}) == []
