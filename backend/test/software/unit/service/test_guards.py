"""app.services.quality.guards - 결정론적 출력 가드(hard/soft) + decide() 게이트.

embedder.cosine_similarity 는 실제 DINO 모델을 로드하므로 전부
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


def _patch_cosine(monkeypatch, *values):
    """embedder.cosine_similarity 호출을 순서대로 values 로 치환
    (run_output_guards 안에선 dino_band 전체 비교 한 번뿐)."""
    it = iter(values)
    monkeypatch.setattr(embedder, "cosine_similarity", lambda o, r, **kw: next(it))


# ── 1. hard 1건 실패 → block ────────────────────────
def test_hard_fail_blocks(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)           # dino_band (soft) 는 정상
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, ["NIKE"], ["ADIDAS"])
    status, failed = guards.decide(guards_out)

    assert status == "block"
    assert [g.name for g in failed] == ["ocr_match"]


# ── 2. soft만 실패 → pass ───────────────────────────
def test_soft_only_fail_passes(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.5)           # dino_band 밖 (soft 실패)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, ["a"], ["a"])
    status, out = guards.decide(guards_out)

    assert status == "pass"
    dino = next(g for g in guards_out if g.name == "dino_band")
    assert dino.passed is False and dino.severity == "soft"


# ── 3. product 없음 → ocr/dino 만으로 pass ─────────────────────
def test_empty_anchors_passes_when_ocr_and_dino_ok(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, ["scratch"], ["scratch"])
    status, out = guards.decide(guards_out)

    assert status == "pass"
    # 좌표 가드는 제거됐다 — product 도 없으면 ocr/added/dino 세 개뿐
    assert [g.name for g in guards_out] == ["ocr_match", "no_added_text", "dino_band"]


# ── 4. added_text 발생 → soft (관측만, 차단 아님) ─────
def test_added_text_alone_is_soft_and_passes(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(
        orig, result, ["brand"], ["brand", "SALE 50%"])
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
        orig, result, ["Brand  Name"], ["brand name"])
    added = next(g for g in guards_out if g.name == "no_added_text")
    assert added.passed is True and added.value == 0.0


def test_added_text_value_counts_text_match_added(monkeypatch, make_png):
    import app.services.quality.metric as metric_mod
    monkeypatch.setattr(metric_mod, "text_match",
                        lambda b, a: {"recall": 1.0, "changed": [], "added": ["x", "y"]})
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()
    guards_out = guards.run_output_guards(orig, result, ["a"], ["a"])
    added = next(g for g in guards_out if g.name == "no_added_text")
    assert added.value == 2.0 and added.passed is False


def test_ocr_order_independent(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()
    guards_out = guards.run_output_guards(
        orig, result, ["NIKE", "AIR", "29"], ["29", "AIR", "NIKE"])
    status, _ = guards.decide(guards_out)
    assert status == "pass"
    assert next(g for g in guards_out if g.name == "ocr_match").value == 1.0


def test_ocr_garbled_line_blocks(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()
    guards_out = guards.run_output_guards(orig, result, ["NIKE"], ["NlKE"])
    status, failed = guards.decide(guards_out)
    assert status == "block"
    assert [g.name for g in failed] == ["ocr_match"]


def test_ocr_all_text_lost_blocks(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()
    guards_out = guards.run_output_guards(orig, result, ["NIKE"], [])
    ocr = next(g for g in guards_out if g.name == "ocr_match")
    assert ocr.value == 0.0 and ocr.passed is False


# ── 5. 경계값 정확 동작 ───────────────────────────────
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

    guards_out = guards.run_output_guards(orig, result, ["a", "b"], ["b", "a"])
    ocr = next(g for g in guards_out if g.name == "ocr_match")

    assert seen == [(["a", "b"], ["b", "a"])]   # 줄 목록 그대로 (이어붙이지 않음)
    assert ocr.passed is passed and ocr.value == recall and ocr.severity == "hard"


# ── 6. tracing 꺼짐 부작용 0 ─────────────────────────
def test_tracing_disabled_no_side_effects(monkeypatch, make_png):
    assert tracing.get_langfuse() is None   # autouse fixture 로 이미 꺼짐 확인
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, ["a"], ["a"])  # 예외 없이 통과
    status, _ = guards.decide(guards_out)

    assert status == "pass"


# ── 8. 정상 전부 → pass ─────────────────────────────
def test_all_normal_passes(monkeypatch, make_png):
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, ["brand"], ["brand"])
    status, out = guards.decide(guards_out)

    assert status == "pass"
    assert [g.name for g in out] == ["ocr_match", "no_added_text", "dino_band"]
    assert all(g.passed for g in out)


# ── 보너스: 데이터 구조/스코프 계약 ───────────────────
def test_decide_returns_only_hard_fails_not_all_guards():
    g_hard_fail = guards.GuardResult("x", False, 0.1, 0.9, "hard")
    g_soft_fail = guards.GuardResult("y", False, 0.1, 0.9, "soft")
    g_pass = guards.GuardResult("z", True, 1.0, 0.9, "hard")

    status, out = guards.decide([g_hard_fail, g_soft_fail, g_pass])

    assert status == "block"
    assert out == [g_hard_fail]


def test_removed_coordinate_guard_api_is_gone():
    """좌표 가드(feature/defect)와 그 상수·헬퍼는 제거됐다 — 되살아나면 알 수 있게."""
    for name in ("FEATURE_SIM_THRESHOLD", "DEFECT_VISIBILITY_THRESHOLD",
                 "_crop", "defect_visibility"):
        assert not hasattr(guards, name), name


# ── dino_band: 계산 실패는 삼키고 가드만 빠진다 (soft) ──
def test_dino_failure_omits_dino_band_without_raising(monkeypatch, make_png):
    def boom(o, r):
        raise RuntimeError("OOM")
    monkeypatch.setattr(embedder, "cosine_similarity", boom)
    orig, result = make_png(), make_png()

    guards_out = guards.run_output_guards(orig, result, ["a"], ["a"])

    assert [g.name for g in guards_out] == ["ocr_match", "no_added_text"]
    assert guards.decide(guards_out)[0] == "pass"


def test_dino_failure_does_not_swallow_hard_ocr_guard_errors(monkeypatch, make_png):
    """hard 가드(ocr_match)의 계산 예외는 그대로 전파 (fail-open 금지) — soft 만 삼킨다."""
    import app.services.quality.metric as metric_mod

    def boom(*a):
        raise RuntimeError("metric broke")
    monkeypatch.setattr(metric_mod, "text_match", boom)
    _patch_cosine(monkeypatch, 0.9)
    orig, result = make_png(), make_png()
    with pytest.raises(RuntimeError):
        guards.run_output_guards(orig, result, ["a"], ["a"])


@pytest.mark.parametrize("value,passed", [(0.75, True), (0.7499, False)])
def test_dino_band_lower_bound(monkeypatch, make_png, value, passed):
    lo, hi = guards.DINO_BAND
    _patch_cosine(monkeypatch, lo if passed else lo - 0.0001)
    orig, result = make_png(), make_png()
    guards_out = guards.run_output_guards(orig, result, [], [])
    dino = next(g for g in guards_out if g.name == "dino_band")
    assert dino.passed is passed and dino.severity == "soft"


# ── item_dino: 누끼 물건끼리 DINO (soft, 판정과 분리) ─────
def test_run_output_guards_has_no_product_param_and_no_item_dino(monkeypatch, make_png):
    import inspect
    assert list(inspect.signature(guards.run_output_guards).parameters) == [
        "orig", "result", "ocr_before", "ocr_after"]
    _patch_cosine(monkeypatch, 0.9)
    out = guards.run_output_guards(make_png(), make_png(), [], [])
    assert not any(g.name in ("item_dino", "product_dino") for g in out)


def test_item_dino_threshold_constant():
    assert guards.ITEM_DINO_THRESHOLD == 0.80
    assert not hasattr(guards, "PRODUCT_DINO_THRESHOLD")


def _cos_spy(monkeypatch, value=0.9, exc=None):
    calls = []

    def cos(o, r, **kw):
        calls.append((o, r, kw))
        if exc is not None:
            raise exc
        return value
    monkeypatch.setattr(embedder, "cosine_similarity", cos)
    return calls


@pytest.mark.parametrize("value,passed", [(0.80, True), (0.7999, False), (1.0, True), (0.0, False)])
def test_item_guard_threshold_boundary(monkeypatch, value, passed):
    _cos_spy(monkeypatch, value)
    g = guards.item_guard((b"o", b"r"))
    assert g == guards.GuardResult(name="item_dino", passed=passed, value=value,
                                   threshold=guards.ITEM_DINO_THRESHOLD, severity="soft")


def test_item_guard_compares_pair_with_item_span_name(monkeypatch):
    calls = _cos_spy(monkeypatch)
    guards.item_guard((b"ORIG_ITEM", b"RESULT_ITEM"))
    assert calls == [(b"ORIG_ITEM", b"RESULT_ITEM", {"name": "item_dino_similarity"})]


def test_item_guard_none_pair_returns_none_without_embedding(monkeypatch):
    calls = _cos_spy(monkeypatch)
    assert guards.item_guard(None) is None
    assert calls == []


@pytest.mark.parametrize("exc", [RuntimeError("OOM"), ValueError("bad image"), TimeoutError()])
def test_item_guard_exception_is_swallowed_returns_none(monkeypatch, exc):
    _cos_spy(monkeypatch, exc=exc)
    assert guards.item_guard((b"o", b"r")) is None


def test_item_guard_failure_is_soft_decide_still_passes(monkeypatch):
    _cos_spy(monkeypatch, 0.1)
    g = guards.item_guard((b"o", b"r"))
    assert g.passed is False and g.severity == "soft"
    status, out = guards.decide([g])
    assert status == "pass" and out == [g]


def test_item_guard_is_scored_to_tracing(monkeypatch):
    seen = []
    monkeypatch.setattr(guards, "score", lambda name, value, **kw: seen.append((name, value, kw)))
    _cos_spy(monkeypatch, 0.83)
    guards.item_guard((b"o", b"r"))
    assert seen == [("item_dino", 0.83, {"data_type": "NUMERIC"})]


@pytest.mark.parametrize("pair,exc", [(None, None), ((b"o", b"r"), RuntimeError("x"))])
def test_item_guard_not_scored_when_omitted(monkeypatch, pair, exc):
    seen = []
    monkeypatch.setattr(guards, "score", lambda *a, **kw: seen.append(a))
    _cos_spy(monkeypatch, exc=exc)
    assert guards.item_guard(pair) is None
    assert seen == []


def test_dino_band_uses_default_span_name(monkeypatch, make_png):
    """이미지 전체 비교는 name 인자 없이(기본 "dino_similarity") — 누끼 span 과 구분."""
    calls = _cos_spy(monkeypatch)
    guards.run_output_guards(make_png(), make_png(), [], [])
    assert len(calls) == 1 and calls[0][2] == {}


# ── embedder.cosine_similarity(name=...) → observe span 이름 ──
def test_cosine_similarity_name_is_observe_span_name(monkeypatch):
    import contextlib
    import numpy as np
    names = []

    @contextlib.contextmanager
    def fake_observe(name, **kw):
        names.append(name)
        yield None
    monkeypatch.setattr(embedder, "observe", fake_observe)
    monkeypatch.setattr(embedder, "_embed_original", lambda b: np.array([1.0, 0.0]))
    monkeypatch.setattr(embedder, "embed", lambda b: np.array([0.6, 0.8]))
    assert embedder.cosine_similarity(b"a", b"b") == pytest.approx(0.6)
    assert embedder.cosine_similarity(b"a", b"b", name="item_dino_similarity") == pytest.approx(0.6)
    assert names == ["dino_similarity", "item_dino_similarity"]


# ── item_patch_guard: 누끼 쌍 패치 유사도 (soft) ──
def _patch_spy(monkeypatch, value=0.97, exc=None):
    calls = []

    def patch(o, r, *a, **kw):
        calls.append((o, r, a, kw))
        if exc is not None:
            raise exc
        return value
    monkeypatch.setattr(embedder, "patch_similarity", patch)
    return calls


def test_item_patch_threshold_constant():
    assert guards.ITEM_PATCH_THRESHOLD == 0.95


@pytest.mark.parametrize("value,passed", [(0.95, True), (0.9499, False), (1.0, True),
                                          (0.0, False), (-0.2, False)])
def test_item_patch_guard_threshold_boundary(monkeypatch, value, passed):
    _patch_spy(monkeypatch, value)
    g = guards.item_patch_guard((b"o", b"r"))
    assert g == guards.GuardResult(name="item_patch", passed=passed, value=value,
                                   threshold=guards.ITEM_PATCH_THRESHOLD, severity="soft")


def test_item_patch_guard_passes_pair_with_default_bg(monkeypatch):
    calls = _patch_spy(monkeypatch)
    guards.item_patch_guard((b"ORIG_ITEM", b"RESULT_ITEM"))
    assert calls == [(b"ORIG_ITEM", b"RESULT_ITEM", (), {})]


def test_item_patch_guard_none_pair_returns_none_without_embedding(monkeypatch):
    calls = _patch_spy(monkeypatch)
    assert guards.item_patch_guard(None) is None
    assert calls == []


@pytest.mark.parametrize("exc", [RuntimeError("OOM"), ValueError("물건 패치가 없음"),
                                 TimeoutError(), ImportError("cv2")])
def test_item_patch_guard_exception_is_swallowed_returns_none(monkeypatch, exc):
    _patch_spy(monkeypatch, exc=exc)
    assert guards.item_patch_guard((b"o", b"r")) is None


def test_item_patch_guard_real_embedder_bad_bytes_returns_none():
    """실제 patch_similarity 에 이미지가 아닌 bytes — 모델 로드 전에 PIL 이 터지고 None."""
    assert guards.item_patch_guard((b"not-an-image", b"x")) is None


def test_item_patch_guard_failure_is_soft_decide_still_passes(monkeypatch):
    _patch_spy(monkeypatch, 0.1)
    g = guards.item_patch_guard((b"o", b"r"))
    assert g.passed is False and g.severity == "soft"
    assert guards.decide([g]) == ("pass", [g])


def test_item_patch_guard_is_scored_to_tracing(monkeypatch):
    seen = []
    monkeypatch.setattr(guards, "score", lambda name, value, **kw: seen.append((name, value, kw)))
    _patch_spy(monkeypatch, 0.87)
    guards.item_patch_guard((b"o", b"r"))
    assert seen == [("item_patch", 0.87, {"data_type": "NUMERIC"})]


@pytest.mark.parametrize("pair,exc", [(None, None), ((b"o", b"r"), RuntimeError("x"))])
def test_item_patch_guard_not_scored_when_omitted(monkeypatch, pair, exc):
    seen = []
    monkeypatch.setattr(guards, "score", lambda *a, **kw: seen.append(a))
    _patch_spy(monkeypatch, exc=exc)
    assert guards.item_patch_guard(pair) is None
    assert seen == []


def test_item_patch_guard_does_not_call_cosine(monkeypatch):
    cos = _cos_spy(monkeypatch)
    _patch_spy(monkeypatch)
    guards.item_patch_guard((b"o", b"r"))
    assert cos == []


# ── local_ocr_guard: 로컬 OCR 줄 단위 recall (soft) ──
@pytest.mark.parametrize("before", [[], None])
def test_local_ocr_guard_empty_before_returns_none_not_scored(monkeypatch, before):
    seen = []
    monkeypatch.setattr(guards, "score", lambda *a, **kw: seen.append(a))
    assert guards.local_ocr_guard(before, ["NIKE"]) is None
    assert seen == []


def test_local_ocr_guard_same_text_passes():
    g = guards.local_ocr_guard(["NIKE", "JUST DO IT"], ["JUST DO IT", "NIKE"])
    assert g == guards.GuardResult(name="ocr_local", passed=True, value=1.0,
                                   threshold=guards.OCR_MATCH_THRESHOLD, severity="soft")


def test_local_ocr_guard_all_lost_fails_soft():
    g = guards.local_ocr_guard(["NIKE"], [])
    assert g.passed is False and g.value == 0.0 and g.severity == "soft"
    assert guards.decide([g])[0] == "pass"


def test_local_ocr_guard_uses_text_match_recall(monkeypatch):
    seen = []

    def fake(before, after):
        seen.append((before, after))
        return {"recall": 0.96, "added": ["X"]}
    monkeypatch.setattr(guards.metric, "text_match", fake)
    g = guards.local_ocr_guard(["A"], ["B"])
    assert seen == [(["A"], ["B"])] and g.value == 0.96 and g.passed is True


@pytest.mark.parametrize("recall,passed", [(0.95, True), (0.9499, False), (1.0, True)])
def test_local_ocr_guard_threshold_boundary(monkeypatch, recall, passed):
    monkeypatch.setattr(guards.metric, "text_match", lambda b, a: {"recall": recall})
    g = guards.local_ocr_guard(["A"], ["A"])
    assert g.passed is passed and g.threshold == 0.95


def test_local_ocr_guard_is_scored(monkeypatch):
    seen = []
    monkeypatch.setattr(guards, "score", lambda name, value, **kw: seen.append((name, value, kw)))
    guards.local_ocr_guard(["NIKE"], ["NIKE"])
    assert seen == [("ocr_local", 1.0, {"data_type": "NUMERIC"})]


def test_local_ocr_guard_metric_exception_propagates(monkeypatch):
    """local_ocr_guard 자체는 예외를 삼키지 않는다 — 호출부(_local_ocr_check)가 삼킨다."""
    def boom(b, a):
        raise ValueError("bad")
    monkeypatch.setattr(guards.metric, "text_match", boom)
    with pytest.raises(ValueError):
        guards.local_ocr_guard(["A"], ["A"])
