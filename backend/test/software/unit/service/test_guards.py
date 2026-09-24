"""app.services.quality.guards - 결정론적 출력 가드(hard/soft) + decide() 게이트.

embedder.cosine_similarity/crop_sim 은 실제 DINO 모델을 로드하므로 전부
monkeypatch - 가드의 로직(임계값 비교, block/pass 판정)만 검증한다.
metric.text_recall 은 순수 difflib 라 실제 함수를 그대로 쓴다(모델 없음).
"""
import pytest

import app.core.tracing as tracing
from app.services.ai import embedder
from app.services.quality import guards


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    """score() 가 실제 네트워크로 나가지 않도록 - tracing.py 의 noop 컨벤션."""
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)


def _anchor(type_, x1=0, y1=0, x2=500, y2=500):
    return {"type": type_, "box": {"x1": x1, "y1": y1, "x2": x2, "y2": y2}}


def _patch_cosine(monkeypatch, *values):
    """embedder.cosine_similarity 호출을 순서대로 values 로 치환
    (defect_visibility 크롭 비교 → dino_band 전체 비교 순으로 호출됨)."""
    it = iter(values)
    monkeypatch.setattr(embedder, "cosine_similarity", lambda o, r: next(it))


def _patch_crop_sim(monkeypatch, value):
    monkeypatch.setattr(embedder, "crop_sim", lambda o, r, box: value, raising=False)


# ── 1. hard 1건 실패 → block ────────────────────────
def test_hard_fail_blocks(monkeypatch, make_png):
    _patch_crop_sim(monkeypatch, 0.5)         # < 0.90 → feature 가드 실패
    _patch_cosine(monkeypatch, 0.9)           # dino_band (soft) 는 정상
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(
        orig, result, [_anchor("feature")], ["a"], ["a"])
    status, failed = guards.decide(guards_out)

    assert status == "block"
    assert any(g.name == "feature_preserved[0]" for g in failed)


# ── 2. soft만 실패 → pass ───────────────────────────
def test_soft_only_fail_passes(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.5)           # dino_band 밖 (soft 실패)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, [], ["a"], ["a"])
    status, out = guards.decide(guards_out)

    assert status == "pass"
    dino = next(g for g in guards_out if g.name == "dino_band")
    assert dino.passed is False and dino.severity == "soft"


# ── 3. anchors 빈 리스트 → pass ─────────────────────
def test_empty_anchors_passes_when_ocr_and_dino_ok(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, [], ["scratch"], ["scratch"])
    status, out = guards.decide(guards_out)

    assert status == "pass"
    assert not any(g.name.startswith(("feature_preserved", "defect_visible")) for g in guards_out)


# ── 4. added_text 발생 → block ──────────────────────
def test_added_text_blocks(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(
        orig, result, [], ["brand"], ["brand", "SALE 50%"])
    status, failed = guards.decide(guards_out)

    assert status == "block"
    assert any(g.name == "no_added_text" for g in failed)


# ── 5. 경계값 정확 동작 ───────────────────────────────
def test_feature_threshold_boundary_0_90_exactly_passes(monkeypatch, make_png):
    _patch_crop_sim(monkeypatch, 0.90)        # 경계값 자체 = 통과
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(
        orig, result, [_anchor("feature")], ["a"], ["a"])
    feature = next(g for g in guards_out if g.name == "feature_preserved[0]")

    assert feature.passed is True


def test_ocr_match_threshold_boundary_0_95_exactly_passes(monkeypatch, make_png):
    import app.services.quality.metric as metric_mod
    monkeypatch.setattr(metric_mod, "text_recall", lambda a, b: 0.95)
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, [], ["a"], ["a"])
    ocr = next(g for g in guards_out if g.name == "ocr_match")

    assert ocr.passed is True


# ── 6. tracing 꺼짐 부작용 0 ─────────────────────────
def test_tracing_disabled_no_side_effects(monkeypatch, make_png):
    assert tracing.get_langfuse() is None   # autouse fixture 로 이미 꺼짐 확인
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, [], ["a"], ["a"])  # 예외 없이 통과
    status, _ = guards.decide(guards_out)

    assert status == "pass"


# ── 7. 결함 앵커 미가시 → block ──────────────────────
def test_defect_anchor_not_visible_blocks(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.5, 0.9)      # 1st: defect crop, 2nd: dino_band
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(
        orig, result, [_anchor("defect")], ["a"], ["a"])
    status, failed = guards.decide(guards_out)

    assert status == "block"
    assert any(g.name == "defect_visible[0]" for g in failed)


# ── 8. 정상 전부 → pass ─────────────────────────────
def test_all_normal_passes(monkeypatch, make_png):
    _patch_crop_sim(monkeypatch, 0.95)
    _patch_cosine(monkeypatch, 0.9, 0.9)      # 1st: defect crop, 2nd: dino_band
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(
        orig, result, [_anchor("feature"), _anchor("defect")], ["brand"], ["brand"])
    status, out = guards.decide(guards_out)

    assert status == "pass"
    assert len(out) == 5   # feature + defect + ocr_match + no_added_text + dino_band
    assert all(g.passed for g in out)


# ── 보너스: 데이터 구조/스코프 계약 ───────────────────
def test_decide_returns_only_hard_fails_not_all_guards():
    g_hard_fail = guards.GuardResult("x", False, 0.1, 0.9, "hard")
    g_soft_fail = guards.GuardResult("y", False, 0.1, 0.9, "soft")
    g_pass = guards.GuardResult("z", True, 1.0, 0.9, "hard")

    status, out = guards.decide([g_hard_fail, g_soft_fail, g_pass])

    assert status == "block"
    assert out == [g_hard_fail]


def test_feature_anchor_without_crop_sim_raises_by_design(make_png):
    """embedder.crop_sim 은 스코프상 이 파일에서 구현하지 않는다 - 실제로
    없으면 AttributeError 로 터지는 게 의도된 동작(조용히 pass 처리하지 않음)."""
    assert not hasattr(embedder, "crop_sim")
    orig, result = make_png(), make_png()

    with pytest.raises(AttributeError):
        guards.run_output_guards(orig, result, [_anchor("feature")], [], [])
