"""재생성(retry) 게이트 — `generate()` 결과가 '제대로 된 사진'이 아니면
(구도 잘림/자막 오버레이) `validate_result`가 감지하고, `_route_after_validate`가
`settings.max_generate_attempts`까지 `generate`를 재시도시키는지 검증한다.

- `pipeline.generate()`: `gen_attempts` 증가가 순수하게 맞는지 (0→1, N→N+1).
- `pipeline._route_after_validate()`: valid/invalid × 시도횟수 경계값 라우팅.
- GRAPH 전체: `check_photo`가 1회 invalid → 2회째 valid 를 리턴하면 실제로
  `generate`가 두 번 호출되는지 (LangGraph 조건부 엣지가 실제로 루프를 도는지).
"""
import pytest

import app.core.tracing as tracing
import app.services.pipeline as pipeline_mod
from app.core.config import settings


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    """.env 에 실제 Langfuse 키가 있어도 테스트에서는 절대 네트워크로 나가지 않도록."""
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)


# ── generate(): gen_attempts 증가 ─────────────────────────────
def test_generate_increments_attempts_from_zero_when_absent(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "_generate_ai", lambda original, preset: b"GEN")
    monkeypatch.setattr(pipeline_mod.storage, "save", lambda *a, **kw: None)

    s = {"original": b"ORIG", "preset": {"prompt": "p"}, "file_id": "fid",
         "preset_key": "preset_a"}
    out = pipeline_mod.generate(s)

    assert out["gen_attempts"] == 1
    assert out["result"] == b"GEN"
    assert out["result_name"] == "fid_preset_a.jpg"


def test_generate_increments_attempts_from_prior_state(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "_generate_ai", lambda original, preset: b"GEN2")
    monkeypatch.setattr(pipeline_mod.storage, "save", lambda *a, **kw: None)

    s = {"original": b"ORIG", "preset": {"prompt": "p"}, "file_id": "fid",
         "preset_key": "preset_a", "gen_attempts": 1}
    out = pipeline_mod.generate(s)

    assert out["gen_attempts"] == 2


def test_generate_attempts_increments_each_call_across_repeated_invocations(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "_generate_ai", lambda original, preset: b"GEN")
    monkeypatch.setattr(pipeline_mod.storage, "save", lambda *a, **kw: None)

    s = {"original": b"ORIG", "preset": {"prompt": "p"}, "file_id": "fid",
         "preset_key": "preset_a"}
    s.update(pipeline_mod.generate(s))
    s.update(pipeline_mod.generate(s))
    s.update(pipeline_mod.generate(s))

    assert s["gen_attempts"] == 3


# ── _route_after_validate(): 라우팅 ────────────────────────────
def test_route_after_validate_valid_photo_returns_ok():
    s = {"photo_check": {"valid": True, "reason": ""}, "gen_attempts": 1}
    assert pipeline_mod._route_after_validate(s) == "ok"


def test_route_after_validate_invalid_with_attempts_below_max_returns_retry(monkeypatch):
    monkeypatch.setattr(settings, "max_generate_attempts", 2, raising=False)
    s = {"photo_check": {"valid": False, "reason": "cropped"}, "gen_attempts": 1}
    assert pipeline_mod._route_after_validate(s) == "retry"


def test_route_after_validate_invalid_at_exact_max_attempts_boundary_returns_ok(monkeypatch):
    """정확히 max_generate_attempts 에 도달하면 한도 소진으로 더 재시도하지
    않고(ok) 마지막 결과로 진행한다."""
    monkeypatch.setattr(settings, "max_generate_attempts", 2, raising=False)
    s = {"photo_check": {"valid": False, "reason": "cropped"}, "gen_attempts": 2}
    assert pipeline_mod._route_after_validate(s) == "ok"


def test_route_after_validate_invalid_above_max_attempts_returns_ok(monkeypatch):
    monkeypatch.setattr(settings, "max_generate_attempts", 2, raising=False)
    s = {"photo_check": {"valid": False, "reason": "cropped"}, "gen_attempts": 3}
    assert pipeline_mod._route_after_validate(s) == "ok"


def test_route_after_validate_missing_photo_check_defaults_ok():
    """photo_check 자체가 없으면(validate_result 실패 시에도 개방형 폴백을
    반환하지만, 상태에 아예 없을 극단적 케이스도) valid 취급 - ok."""
    assert pipeline_mod._route_after_validate({"gen_attempts": 0}) == "ok"


def test_route_after_validate_missing_gen_attempts_defaults_zero(monkeypatch):
    """gen_attempts 가 없으면 0 으로 취급 - max=1 이면 이미 한도 도달과 동일해
    바로 ok 로 소진 처리된다."""
    monkeypatch.setattr(settings, "max_generate_attempts", 0, raising=False)
    s = {"photo_check": {"valid": False, "reason": "cropped"}}
    assert pipeline_mod._route_after_validate(s) == "ok"


def test_route_after_validate_default_max_generate_attempts_is_two():
    """설정 기본값 회귀 방지: max_generate_attempts 기본값이 2(최초 포함)."""
    assert settings.max_generate_attempts == 2


# ── GRAPH 전체: 실제 LangGraph 조건부 엣지가 루프를 도는지 ─────
def test_graph_retries_generate_once_when_check_photo_invalid_then_valid(monkeypatch, make_png):
    """check_photo 가 첫 결과는 invalid, 두 번째 결과는 valid 를 리턴하면
    generate 가 정확히 두 번 호출되고(재생성 1회), 최종 gen_attempts=2 로
    끝까지(run_judge/finalize) 통과하는지 - 라우팅 함수 단위 테스트만으론
    LangGraph 조건부 엣지 배선 자체(문자열 오타 등)는 검증되지 않으므로 필요."""
    monkeypatch.setattr(settings, "max_generate_attempts", 2, raising=False)

    from app.services.persistence import storage
    storage.save("original", "fid-retry.jpg", make_png())

    monkeypatch.setattr(pipeline_mod.detector, "classify",
                         lambda img: {"item": "chair", "considered": []})
    monkeypatch.setattr(pipeline_mod.detector, "detect_defects",
                         lambda img, item, considered, **kw: [])   # strict=True 받음

    gen_calls = {"n": 0}

    def fake_generate_ai(original, preset):
        gen_calls["n"] += 1
        return make_png(color=(gen_calls["n"] * 10, 0, 0))
    monkeypatch.setattr(pipeline_mod, "_generate_ai", fake_generate_ai)

    check_calls = {"n": 0}

    def fake_check_photo(image_bytes):
        check_calls["n"] += 1
        if check_calls["n"] == 1:
            return {"valid": False, "reason": "cropped"}
        return {"valid": True, "reason": ""}
    monkeypatch.setattr(pipeline_mod.detector, "check_photo", fake_check_photo)

    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity",
                         lambda orig, result: 0.9)
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate",
                         lambda img, anchors, item, considered: [])
    monkeypatch.setattr(pipeline_mod.judge, "judge",
                         lambda orig, result: {"analysis": "ok", "fidelity": 5,
                                                "realism": 5, "trust": 5})

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-retry", "preset_key": "studio_white"})

    assert gen_calls["n"] == 2
    assert check_calls["n"] == 2
    assert out["gen_attempts"] == 2
    assert out["photo_check"] == {"valid": True, "reason": ""}


def test_graph_stops_retrying_after_max_attempts_when_always_invalid(monkeypatch, make_png):
    """check_photo 가 매번 invalid 를 리턴해도 max_generate_attempts 를 넘어서면
    루프를 멈추고 마지막 결과로 파이프라인을 끝까지 진행한다(무한루프 방지)."""
    monkeypatch.setattr(settings, "max_generate_attempts", 2, raising=False)

    from app.services.persistence import storage
    storage.save("original", "fid-retry-exhaust.jpg", make_png())

    monkeypatch.setattr(pipeline_mod.detector, "classify",
                         lambda img: {"item": "chair", "considered": []})
    monkeypatch.setattr(pipeline_mod.detector, "detect_defects",
                         lambda img, item, considered, **kw: [])   # strict=True 받음

    gen_calls = {"n": 0}

    def fake_generate_ai(original, preset):
        gen_calls["n"] += 1
        return make_png(color=(gen_calls["n"] * 10, 0, 0))
    monkeypatch.setattr(pipeline_mod, "_generate_ai", fake_generate_ai)

    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                         lambda image_bytes: {"valid": False, "reason": "always cropped"})
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity",
                         lambda orig, result: 0.9)
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate",
                         lambda img, anchors, item, considered: [])
    monkeypatch.setattr(pipeline_mod.judge, "judge",
                         lambda orig, result: {"analysis": "ok", "fidelity": 5,
                                                "realism": 5, "trust": 5})

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-retry-exhaust", "preset_key": "studio_white"})

    assert gen_calls["n"] == settings.max_generate_attempts == 2
    assert out["gen_attempts"] == 2
    assert out["photo_check"] == {"valid": False, "reason": "always cropped"}
