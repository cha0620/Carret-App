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


# ── 4. added_text 발생 → soft (관측만, 차단 아님) ─────
def test_added_text_alone_is_soft_and_passes(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(
        orig, result, [], ["brand"], ["brand", "SALE 50%"])
    status, failed = guards.decide(guards_out)

    assert status == "pass"
    added = next(g for g in guards_out if g.name == "no_added_text")
    assert added.passed is False and added.severity == "soft" and added.value == 1.0
    ocr = next(g for g in guards_out if g.name == "ocr_match")
    assert ocr.passed is True


def test_added_text_uses_text_match_added_not_set_difference(monkeypatch, make_png):
    """정확 일치 집합 차가 아니라 text_match 의 added — 대소문자·공백만 다른 줄은 새 글자가 아님."""
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()
    guards_out = guards.run_output_guards(
        orig, result, [], ["Brand  Name"], ["brand name"])
    added = next(g for g in guards_out if g.name == "no_added_text")
    assert added.passed is True and added.value == 0.0


def test_added_text_value_counts_text_match_added(monkeypatch, make_png):
    import app.services.quality.metric as metric_mod
    monkeypatch.setattr(metric_mod, "text_match",
                        lambda b, a: {"recall": 1.0, "changed": [], "added": ["x", "y"]})
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()
    guards_out = guards.run_output_guards(orig, result, [], ["a"], ["a"])
    added = next(g for g in guards_out if g.name == "no_added_text")
    assert added.value == 2.0 and added.passed is False


def test_ocr_order_independent(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()
    guards_out = guards.run_output_guards(
        orig, result, [], ["NIKE", "AIR", "29"], ["29", "AIR", "NIKE"])
    status, _ = guards.decide(guards_out)
    assert status == "pass"
    assert next(g for g in guards_out if g.name == "ocr_match").value == 1.0


def test_ocr_garbled_line_blocks(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()
    guards_out = guards.run_output_guards(orig, result, [], ["NIKE"], ["NlKE"])
    status, failed = guards.decide(guards_out)
    assert status == "block"
    assert [g.name for g in failed] == ["ocr_match"]


def test_ocr_all_text_lost_blocks(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()
    guards_out = guards.run_output_guards(orig, result, [], ["NIKE"], [])
    ocr = next(g for g in guards_out if g.name == "ocr_match")
    assert ocr.value == 0.0 and ocr.passed is False


# ── 5. 경계값 정확 동작 ───────────────────────────────
def test_feature_threshold_boundary_0_90_exactly_passes(monkeypatch, make_png):
    _patch_crop_sim(monkeypatch, 0.90)        # 경계값 자체 = 통과
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(
        orig, result, [_anchor("feature")], ["a"], ["a"])
    feature = next(g for g in guards_out if g.name == "feature_preserved[0]")

    assert feature.passed is True


@pytest.mark.parametrize("recall,passed", [(0.95, True), (0.949, False), (1.0, True)])
def test_ocr_match_threshold_boundary_0_95(monkeypatch, make_png, recall, passed):
    """ocr_match 는 text_match 의 recall 로 판정 (text_recall 아님)."""
    import app.services.quality.metric as metric_mod
    seen = []
    monkeypatch.setattr(metric_mod, "text_match",
                        lambda b, a: seen.append((b, a)) or
                        {"recall": recall, "changed": [], "added": []})

    def poison(*a):
        raise AssertionError("text_recall 은 더 이상 쓰지 않는다")
    monkeypatch.setattr(metric_mod, "text_recall", poison)
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, [], ["a", "b"], ["b", "a"])
    ocr = next(g for g in guards_out if g.name == "ocr_match")

    assert seen == [(["a", "b"], ["b", "a"])]   # 줄 목록 그대로 (이어붙이지 않음)
    assert ocr.passed is passed and ocr.value == recall and ocr.severity == "hard"


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


# ── dino_band: 계산 실패는 삼키고 가드만 빠진다 (soft) ──
def test_dino_failure_omits_dino_band_without_raising(monkeypatch, make_png):
    def boom(o, r):
        raise RuntimeError("OOM")
    monkeypatch.setattr(embedder, "cosine_similarity", boom)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, [], ["a"], ["a"])

    assert [g.name for g in guards_out] == ["ocr_match", "no_added_text"]
    assert guards.decide(guards_out)[0] == "pass"


def test_dino_failure_does_not_swallow_hard_defect_guard_errors(monkeypatch, make_png):
    """defect 가시성(hard)도 cosine 을 쓰지만 그 예외는 그대로 전파 (fail-open 금지)."""
    def boom(o, r):
        raise RuntimeError("OOM")
    monkeypatch.setattr(embedder, "cosine_similarity", boom)
    orig, result = make_png(), make_png()
    with pytest.raises(RuntimeError):
        guards.run_output_guards(orig, result, [_anchor("defect")], [], [])


@pytest.mark.parametrize("value,passed", [(0.75, True), (0.7499, False)])
def test_dino_band_lower_bound(monkeypatch, make_png, value, passed):
    lo, hi = guards.DINO_BAND
    _patch_cosine(monkeypatch, lo if passed else lo - 0.0001)
    orig, result = make_png(), make_png()
    guards_out = guards.run_output_guards(orig, result, [], [], [])
    dino = next(g for g in guards_out if g.name == "dino_band")
    assert dino.passed is passed and dino.severity == "soft"
