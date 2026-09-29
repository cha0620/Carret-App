"""app.services.quality.guards - 결정론적 출력 가드 (전부 soft, 관측용).

VLM OCR hard 가드(ocr_match·no_added_text)·run_output_guards·decide 는 09-27 에 삭제 —
이미지 전체 DINO 는 dino_band_guard 하나로 남았다.

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
    (dino_band_guard 는 이미지 전체 비교 한 번뿐)."""
    it = iter(values)
    monkeypatch.setattr(embedder, "cosine_similarity", lambda o, r, **kw: next(it))


# ── 보너스: 데이터 구조/스코프 계약 ───────────────────


# ── item_dino: 누끼 물건끼리 DINO (soft, 판정과 분리) ─────


def test_item_dino_threshold_constant():
    assert guards.ITEM_DINO_THRESHOLD == 0.80


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


def test_item_guard_failure_is_soft(monkeypatch):
    _cos_spy(monkeypatch, 0.1)
    g = guards.item_guard((b"o", b"r"))
    assert g.passed is False and g.severity == "soft"
    # soft — 판정(막기)은 없다


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


def test_item_patch_guard_failure_is_soft(monkeypatch):
    _patch_spy(monkeypatch, 0.1)
    g = guards.item_patch_guard((b"o", b"r"))
    assert g.passed is False and g.severity == "soft"


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


def test_no_hard_guard_left_in_module_constants():
    # 로컬 OCR recall 기준은 관측용으로 남는다
    assert guards.OCR_MATCH_THRESHOLD == 0.95
    assert guards.DINO_BAND == (0.75, 0.995)


# ── dino_band_guard: 이미지 전체 DINO (soft), 계산 실패는 None ──
def test_dino_band_guard_signature():
    import inspect
    assert list(inspect.signature(guards.dino_band_guard).parameters) == ["orig", "result"]


@pytest.mark.parametrize("value,passed", [
    (0.75, True), (0.7499, False), (0.995, True), (0.9951, False),
    (0.88, True), (1.0, False), (0.0, False), (-0.3, False)])
def test_dino_band_guard_band_boundaries(monkeypatch, value, passed):
    _patch_cosine(monkeypatch, value)
    g = guards.dino_band_guard(b"o", b"r")
    lo, _ = guards.DINO_BAND
    assert g == guards.GuardResult(name="dino_band", passed=passed, value=value,
                                   threshold=lo, severity="soft")


def test_dino_band_guard_compares_orig_and_result_with_default_span_name(monkeypatch):
    # 이미지 전체 비교는 name 인자 없이(기본 "dino_similarity") — 누끼 span 과 구분
    calls = _cos_spy(monkeypatch)
    guards.dino_band_guard(b"ORIG", b"RESULT")
    assert calls == [(b"ORIG", b"RESULT", {})]


@pytest.mark.parametrize("exc", [RuntimeError("OOM"), ValueError("bad"), TimeoutError(),
                                 MemoryError()])
def test_dino_band_guard_exception_returns_none(monkeypatch, exc, capsys):
    _cos_spy(monkeypatch, exc=exc)
    assert guards.dino_band_guard(b"o", b"r") is None
    assert "dino_band 계산 실패" in capsys.readouterr().out


def test_dino_band_guard_base_exception_is_not_swallowed(monkeypatch):
    # Exception 만 삼킨다 — KeyboardInterrupt 같은 BaseException 은 그대로
    _cos_spy(monkeypatch, exc=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        guards.dino_band_guard(b"o", b"r")


def test_dino_band_guard_is_scored(monkeypatch):
    seen = []
    monkeypatch.setattr(guards, "score", lambda name, value, **kw: seen.append((name, value, kw)))
    _patch_cosine(monkeypatch, 0.81)
    guards.dino_band_guard(b"o", b"r")
    assert seen == [("dino_band", 0.81, {"data_type": "NUMERIC"})]


def test_dino_band_guard_not_scored_on_failure(monkeypatch):
    seen = []
    monkeypatch.setattr(guards, "score", lambda *a, **kw: seen.append(a))
    _cos_spy(monkeypatch, exc=RuntimeError("x"))
    assert guards.dino_band_guard(b"o", b"r") is None
    assert seen == []


def test_dino_band_guard_tracing_disabled_no_side_effects(monkeypatch):
    assert tracing.get_langfuse() is None   # autouse fixture 로 이미 꺼짐
    _patch_cosine(monkeypatch, 0.9)
    assert guards.dino_band_guard(b"o", b"r").passed is True


def test_dino_band_guard_does_not_touch_text_metric(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("dino_band 는 글자를 비교하지 않는다")
    monkeypatch.setattr(guards.metric, "text_match", boom)
    _patch_cosine(monkeypatch, 0.9)
    assert guards.dino_band_guard(b"o", b"r") is not None


@pytest.mark.parametrize("fn,args", [
    ("dino_band_guard", (b"o", b"r")), ("item_guard", ((b"o", b"r"),)),
    ("item_patch_guard", ((b"o", b"r"),)), ("local_ocr_guard", (["NIKE"], []))])
def test_every_guard_is_soft(monkeypatch, fn, args):
    _cos_spy(monkeypatch, 0.1)
    _patch_spy(monkeypatch, 0.1)
    g = getattr(guards, fn)(*args)
    assert g.severity == "soft" and g.passed is False


def test_metric_punctuation_variants_match():
    # 읽기 흔들림("H.M"/"H-M", 쪼개 읽기)은 같은 글자로 본다 (로컬 OCR 가드가 쓰는 metric)
    m = guards.metric.text_match(["H.M", "00 3060", "OFFICIAL"], ["h-m", "00", "3060", "official"])
    assert m["recall"] >= guards.OCR_MATCH_THRESHOLD and m["added"] == []
