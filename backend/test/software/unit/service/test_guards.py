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


def _cos_spy(monkeypatch, value=0.9, exc=None):
    calls = []

    def cos(o, r, **kw):
        calls.append((o, r, kw))
        if exc is not None:
            raise exc
        return value
    monkeypatch.setattr(embedder, "cosine_similarity", cos)
    return calls


@pytest.mark.parametrize("value,passed", [(0.80, True), (0.7999, False), (1.0, True),])
def test_item_guard_threshold_boundary(monkeypatch, value, passed):
    _cos_spy(monkeypatch, value)
    g = guards.item_guard((b"o", b"r"))
    assert g == guards.GuardResult(name="item_dino", passed=passed, value=value,
                                   threshold=guards.ITEM_DINO_THRESHOLD, severity="soft")


@pytest.mark.parametrize("exc", [RuntimeError("OOM"), ValueError("bad image"), TimeoutError()])
def test_item_guard_exception_is_swallowed_returns_none(monkeypatch, exc):
    _cos_spy(monkeypatch, exc=exc)
    assert guards.item_guard((b"o", b"r")) is None


    # soft — 판정(막기)은 없다


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


@pytest.mark.parametrize("value,passed", [(0.95, True), (0.9499, False), (1.0, True),])
def test_item_patch_guard_threshold_boundary(monkeypatch, value, passed):
    _patch_spy(monkeypatch, value)
    g = guards.item_patch_guard((b"o", b"r"))
    assert g == guards.GuardResult(name="item_patch", passed=passed, value=value,
                                   threshold=guards.ITEM_PATCH_THRESHOLD, severity="soft")


def test_item_patch_guard_real_embedder_bad_bytes_returns_none():
    """실제 patch_similarity 에 이미지가 아닌 bytes — 모델 로드 전에 PIL 이 터지고 None."""
    assert guards.item_patch_guard((b"not-an-image", b"x")) is None


def test_local_ocr_guard_uses_text_match_recall(monkeypatch):
    seen = []

    def fake(before, after):
        seen.append((before, after))
        return {"recall": 0.96, "added": ["X"]}
    monkeypatch.setattr(guards.metric, "text_match", fake)
    g = guards.local_ocr_guard(["A"], ["B"])
    assert seen == [(["A"], ["B"])] and g.value == 0.96 and g.passed is True


def test_local_ocr_guard_metric_exception_propagates(monkeypatch):
    """local_ocr_guard 자체는 예외를 삼키지 않는다 — 호출부(_local_ocr_check)가 삼킨다."""
    def boom(b, a):
        raise ValueError("bad")
    monkeypatch.setattr(guards.metric, "text_match", boom)
    with pytest.raises(ValueError):
        guards.local_ocr_guard(["A"], ["A"])


@pytest.mark.parametrize("value,passed", [
    (0.75, True), (0.7499, False), (0.995, True),])
def test_dino_band_guard_band_boundaries(monkeypatch, value, passed):
    _patch_cosine(monkeypatch, value)
    g = guards.dino_band_guard(b"o", b"r")
    lo, _ = guards.DINO_BAND
    assert g == guards.GuardResult(name="dino_band", passed=passed, value=value,
                                   threshold=lo, severity="soft")


def test_dino_band_guard_base_exception_is_not_swallowed(monkeypatch):
    # Exception 만 삼킨다 — KeyboardInterrupt 같은 BaseException 은 그대로
    _cos_spy(monkeypatch, exc=KeyboardInterrupt())
    with pytest.raises(KeyboardInterrupt):
        guards.dino_band_guard(b"o", b"r")


@pytest.mark.parametrize("fn,args", [
    ("dino_band_guard", (b"o", b"r")), ("item_guard", ((b"o", b"r"),)),
    ("item_patch_guard", ((b"o", b"r"),)),])
def test_every_guard_is_soft(monkeypatch, fn, args):
    _cos_spy(monkeypatch, 0.1)
    _patch_spy(monkeypatch, 0.1)
    g = getattr(guards, fn)(*args)
    assert g.severity == "soft" and g.passed is False


