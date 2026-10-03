"""app.services.pipeline — 실행 환경 쪽 약속 (경로와 무관).

- validate_result: 관측 신호를 백그라운드로 돌리고, 늦거나 터지면 기다리지 않고 취소한다
- judge_and_save: 채점 중 재변환이 끼어들면 옛 점수를 남기지 않는다
- run_transform 껍데기: 트레이스 출력 · 예외여도 flush · 그래프 도중 끼어든 옛 성적표 삭제

경로는 test_pipeline_scenarios.py, 순수 규칙은 test_pipeline_rules.py.
"""
import contextvars
import io
import json
import threading
import time
import types

import pytest
from PIL import Image

import app.core.tracing as tracing
import app.services.pipeline as pipeline_mod
from app.core.config import settings
from app.prompts.rubric import AXES
from app.services.persistence import storage


def _png(color):
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buf, format="PNG")
    return buf.getvalue()


ORIG_PNG, GEN_PNG, NEW_PNG = _png((120, 90, 60)), _png((250, 250, 250)), _png((9, 9, 9))


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    monkeypatch.setattr(settings, "pipeline_mode", "real", raising=False)
    monkeypatch.setattr(settings, "local_ocr_guard", False)


# ══ validate_result: 백그라운드 관측 신호 ══════════════════════════════
def _state(**kw):
    return {"file_id": "v", "preset_key": "p", "result_name": "v_p.jpg",
            "original": ORIG_PNG, "result": GEN_PNG, **kw}


class _Future:
    def __init__(self, value=None, exc=None, on_result=None):
        self.value, self.exc, self.on_result = value, exc, on_result
        self.cancelled, self.timeouts = 0, []

    def cancel(self):
        self.cancelled += 1
        return True

    def result(self, timeout=None):
        self.timeouts.append(timeout)
        if self.on_result:
            self.on_result()
        if self.exc is not None:
            raise self.exc
        return self.value


def _background(monkeypatch, **futures):
    """_in_background 가짜 — 제출한 함수 이름으로 Future 를 고른다."""
    defaults = {"check_photo": {"valid": True, "reason": ""}, "_local_ocr_lines": ([], [])}
    fs = {name: futures.get(name) or _Future(value) for name, value in defaults.items()}
    submitted = []

    def fake(fn, *a, **kw):
        submitted.append(fn.__name__)
        return fs[fn.__name__]
    monkeypatch.setattr(pipeline_mod, "_in_background", fake)
    return fs, submitted


@pytest.mark.parametrize("exc,ocr_on", [(RuntimeError("embedder down"), True),
                                        (KeyboardInterrupt(), False)])
def test_validate_result_crash_cancels_started_work_and_saves_nothing(monkeypatch, exc, ocr_on):
    monkeypatch.setattr(settings, "local_ocr_guard", ocr_on)
    fs, submitted = _background(monkeypatch)

    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(pipeline_mod.guards, "dino_band_guard", boom)

    with pytest.raises(type(exc)):
        pipeline_mod.validate_result(_state(item_texts=[{"text": "NIKE"}]))
    assert submitted == ["check_photo"] + (["_local_ocr_lines"] if ocr_on else [])
    assert all(fs[name].cancelled == 1 for name in submitted)
    assert storage.load("result", "v_p.jpg") is None


def test_validate_result_saves_result_before_waiting(monkeypatch):
    seen = []
    photo = _Future({"valid": True, "reason": ""},
                    on_result=lambda: seen.append(storage.load("result", "v_p.jpg") is not None))
    _background(monkeypatch, check_photo=photo)
    monkeypatch.setattr(pipeline_mod.guards, "dino_band_guard", lambda *a: None)
    pipeline_mod.validate_result(_state())
    assert seen == [True]


def test_validate_result_waits_for_photo_up_to_vlm_timeout(monkeypatch):
    monkeypatch.setattr(settings, "vlm_timeout_s", 7)
    fs, _ = _background(monkeypatch, check_photo=_Future({"valid": False, "reason": "cropped"}))
    monkeypatch.setattr(pipeline_mod.guards, "dino_band_guard", lambda *a: None)
    out = pipeline_mod.validate_result(_state())
    assert fs["check_photo"].timeouts == [17]
    assert out["photo_check"] == {"valid": False, "reason": "cropped"}


def test_validate_result_photo_timeout_counts_as_valid_in_real_pool(monkeypatch):
    """check_photo 가 상한보다 늦으면 기다리지 않는다 — 실패 정책(개방형 valid)과 같게."""
    release = threading.Event()
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: release.wait(5) and {"valid": False, "reason": "late"})
    monkeypatch.setattr(pipeline_mod.guards, "dino_band_guard", lambda *a: None)
    monkeypatch.setattr(pipeline_mod, "_item_signals", lambda s: (None, None))
    monkeypatch.setattr(settings, "vlm_timeout_s", -9.95)     # 상한 0.05초 (ge=1 검증 우회)
    try:
        out = pipeline_mod.validate_result(_state())
    finally:
        release.set()
    assert out["photo_check"] == {"valid": True, "reason": ""}


def test_validate_result_deadlines_start_at_submit_not_after_photo(monkeypatch):
    """로컬 OCR 대기 상한은 작업을 넘긴 시점부터 — check_photo 를 기다린 만큼 줄어든다."""
    clock = [100.0]
    monkeypatch.setattr(pipeline_mod, "time", types.SimpleNamespace(
        time=time.time, sleep=lambda s: None, monotonic=lambda: clock[0]))
    monkeypatch.setattr(settings, "local_ocr_guard", True)
    photo = _Future({"valid": True, "reason": ""}, on_result=lambda: clock.__setitem__(0, 112.0))
    fs, _ = _background(monkeypatch, check_photo=photo)
    monkeypatch.setattr(pipeline_mod.guards, "dino_band_guard", lambda *a: None)

    pipeline_mod.validate_result(_state(item_texts=[{"text": "NIKE"}]))

    assert fs["_local_ocr_lines"].timeouts == [pipeline_mod.LOCAL_OCR_WAIT_S - 12]


def test_validate_result_does_not_wait_for_cutout(monkeypatch):
    """누끼 비교는 그래프 밖 — validate_result 는 오리기를 시작하지도 기다리지도 않는다 (09-29: 3~43초)."""
    def poison(*a, **k):
        raise AssertionError("validate_result 가 누끼를 오렸다")
    monkeypatch.setattr(pipeline_mod.compositor, "isolate", poison)
    _background(monkeypatch)
    monkeypatch.setattr(pipeline_mod.guards, "dino_band_guard", lambda *a: None)
    out = pipeline_mod.validate_result(_state())
    assert "item_similarity" not in out and out["photo_check"]["valid"] is True


@pytest.mark.parametrize("make_future,expected_cancel", [
    (lambda: _Future(exc=RuntimeError("easyocr")), 1),     # 실패 — 취소하고 생략
    (lambda: _Future(([], ["X"])), 0),                     # 원본에 읽은 글자 없음 — 가드 없음
])
def test_validate_result_local_ocr_failure_or_nothing_read_gives_none(monkeypatch, make_future,
                                                                      expected_cancel):
    ocr_future = make_future()
    monkeypatch.setattr(settings, "local_ocr_guard", True)
    _background(monkeypatch, _local_ocr_lines=ocr_future)
    monkeypatch.setattr(pipeline_mod.guards, "dino_band_guard", lambda *a: None)
    out = pipeline_mod.validate_result(_state(item_texts=[{"text": "NIKE"}]))
    assert out["ocr_local_recall"] is None and out["guard_report"] == []
    assert ocr_future.cancelled == expected_cancel


def test_in_background_copies_context_to_worker(monkeypatch):
    """트레이스 컨텍스트가 백그라운드 VLM 호출까지 이어지고, 거기서 바꾼 값은 새지 않는다."""
    var = contextvars.ContextVar("trace_ctx_test", default="unset")

    def work():
        before = var.get()
        var.set("inner")
        return before, threading.current_thread().name

    token = var.set("trace-abc")
    try:
        before, thread = pipeline_mod._in_background(work).result(timeout=5)
        assert var.get() == "trace-abc"
    finally:
        var.reset(token)
    assert before == "trace-abc" and thread.startswith("pipeline")


# ══ item_signals_and_save: 누끼 비교 (그래프 밖, 응답 뒤) ═══════════════
@pytest.fixture()
def items(monkeypatch):
    """원본·결과·inspect 를 저장하고, 오리기·DINO 를 가짜로."""
    storage.save("original", "fis.png", ORIG_PNG)
    storage.save("result", "fis_p.jpg", GEN_PNG)
    rec = {"isolate": [], "flush": 0, "sim": 0.9, "patch": 0.97}

    def write_inspect(**kw):
        storage.save("quality", "fis_p_inspect.json", json.dumps(
            {"mode": "generate", "item_box": {"x1": 1, "y1": 2, "x2": 3, "y2": 4},
             "item_similarity": None, "guard_report": [{"name": "dino_band"}], **kw}).encode())
    rec["write_inspect"] = write_inspect
    write_inspect()

    def isolate(img, box=None, original=False):
        rec["isolate"].append((original, box))
        return b"ISO_o" if original else b"ISO_r"
    monkeypatch.setattr(pipeline_mod.compositor, "isolate", isolate)
    monkeypatch.setattr(pipeline_mod.guards.embedder, "cosine_similarity",
                        lambda o, r, name="": rec["sim"])
    monkeypatch.setattr(pipeline_mod.guards.embedder, "patch_similarity",
                        lambda o, r, bg=(128, 128, 128): rec["patch"])
    monkeypatch.setattr(pipeline_mod, "flush", lambda: rec.__setitem__("flush", rec["flush"] + 1))
    return rec


def _inspect():
    return json.loads(storage.load("quality", "fis_p_inspect.json"))


def test_item_signals_fill_inspect_and_append_failed_guards(items):
    items["sim"] = 0.4                                     # item_dino 기준 밖
    pipeline_mod.item_signals_and_save("fis", "p")
    ins = _inspect()
    assert ins["item_similarity"] == 0.4 and ins["item_patch_similarity"] == 0.97
    assert [g["name"] for g in ins["guard_report"]] == ["dino_band", "item_dino"]
    assert items["isolate"] == [(True, {"x1": 1, "y1": 2, "x2": 3, "y2": 4}), (False, None)]
    assert items["flush"] == 1


@pytest.mark.parametrize("mode", ["composite", "original"])
def test_item_signals_skip_when_result_is_not_generated(items, mode):
    items["write_inspect"](mode=mode)
    pipeline_mod.item_signals_and_save("fis", "p")
    assert items["isolate"] == [] and _inspect()["item_similarity"] is None


@pytest.mark.parametrize("what", ["result", "inspect"])
def test_item_signals_discard_when_retransformed_meanwhile(items, monkeypatch, what):
    real = pipeline_mod.guards.item_patch_guard

    def retransform(pair):
        if what == "result":
            storage.save("result", "fis_p.jpg", NEW_PNG)
        else:
            items["write_inspect"](mode="generate", note="new run")
        return real(pair)
    monkeypatch.setattr(pipeline_mod.guards, "item_patch_guard", retransform)
    pipeline_mod.item_signals_and_save("fis", "p")
    assert _inspect()["item_similarity"] is None


def test_item_signals_failure_is_swallowed(items, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("rembg")
    monkeypatch.setattr(pipeline_mod, "_item_signals", boom)
    pipeline_mod.item_signals_and_save("fis", "p")
    assert _inspect()["item_similarity"] is None and items["flush"] == 1


def test_run_transform_defers_item_signals_for_generated_results(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out(mode="generate")))
    called = []
    monkeypatch.setattr(pipeline_mod, "item_signals_and_save", lambda *a, **k: called.append(a))
    out = pipeline_mod.run_transform("f", "p", defer_judge=True)
    assert out["item_signals_pending"] is True and called == []
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out(mode="composite")))
    assert pipeline_mod.run_transform("f", "p", defer_judge=True)["item_signals_pending"] is False


# ══ judge_and_save: 채점 중 재변환 ═══════════════════════════════════
REPORT = {"analysis": "bg", "fidelity": 4, "realism": 3, "trust": 5}


@pytest.fixture()
def jl(monkeypatch):
    storage.save("original", "fjl.png", ORIG_PNG)
    storage.save("result", "fjl_p.jpg", GEN_PNG)
    rec = {"flush": 0, "scores": [], "judged": 0}
    monkeypatch.setattr(pipeline_mod, "flush", lambda: rec.__setitem__("flush", rec["flush"] + 1))
    monkeypatch.setattr(pipeline_mod, "score", lambda name, v, **kw: rec["scores"].append((name, v)))

    def judge(o, r):
        rec["judged"] += 1
        return dict(REPORT)
    monkeypatch.setattr(pipeline_mod.judge, "judge", judge)
    return rec


def _quality():
    data = storage.load("quality", "fjl_p.json")
    return json.loads(data) if data else None


def test_judge_and_save_saves_report_and_scores_each_axis(jl):
    storage.save("quality", "fjl_p.json", b'{"stale": true}')    # 있어도 다시 채점
    pipeline_mod.judge_and_save("fjl", "p")
    assert jl["judged"] == 1 and _quality() == REPORT
    assert jl["scores"] == [(a, REPORT[a]) for a in AXES] and jl["flush"] == 1


@pytest.mark.parametrize("race", ["during_judge", "after_save", "deleted"])
def test_judge_and_save_drops_score_when_result_changes(jl, monkeypatch, tmp_storage, race):
    real_save = storage.save
    if race == "during_judge":
        monkeypatch.setattr(pipeline_mod.judge, "judge",
                            lambda o, r: real_save("result", "fjl_p.jpg", NEW_PNG) or dict(REPORT))
    elif race == "deleted":
        monkeypatch.setattr(pipeline_mod.judge, "judge",
                            lambda o, r: (tmp_storage / "result" / "fjl_p.jpg").unlink() or dict(REPORT))
    else:
        def racing_save(kind, name, data):
            real_save(kind, name, data)
            if kind == "quality":
                real_save("result", "fjl_p.jpg", NEW_PNG)
        monkeypatch.setattr(pipeline_mod.storage, "save", racing_save)

    pipeline_mod.judge_and_save("fjl", "p")

    assert _quality() is None and jl["scores"] == [] and jl["flush"] == 1


def test_judge_and_save_race_cleanup_failure_is_swallowed(jl, monkeypatch):
    real_save = storage.save

    def racing_save(kind, name, data):
        real_save(kind, name, data)
        if kind == "quality":
            real_save("result", "fjl_p.jpg", NEW_PNG)
    monkeypatch.setattr(pipeline_mod.storage, "save", racing_save)
    monkeypatch.setattr(pipeline_mod.storage, "delete",
                        lambda k, n: (_ for _ in ()).throw(PermissionError("denied")))
    pipeline_mod.judge_and_save("fjl", "p")
    assert jl["scores"] == [] and jl["flush"] == 1


@pytest.mark.parametrize("missing", ["original", "result"])
def test_judge_and_save_missing_input_does_nothing(jl, tmp_storage, missing):
    (tmp_storage / missing / ("fjl.png" if missing == "original" else "fjl_p.jpg")).unlink()
    pipeline_mod.judge_and_save("fjl", "p")
    assert jl["judged"] == 0 and _quality() is None and jl["flush"] == 1


def test_judge_and_save_empty_report_saves_nothing(jl, monkeypatch):
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: {})
    pipeline_mod.judge_and_save("fjl", "p")
    assert _quality() is None and jl["scores"] == []


@pytest.mark.parametrize("where", ["judge", "load", "save"])
def test_judge_and_save_swallows_failures_and_flushes(jl, monkeypatch, where):
    def boom(*a, **k):
        raise RuntimeError(where)
    target = {"judge": (pipeline_mod.judge, "judge"),
              "load": (pipeline_mod.storage, "load_original"),
              "save": (pipeline_mod.storage, "save")}[where]
    monkeypatch.setattr(*target, boom)
    pipeline_mod.judge_and_save("fjl", "p")
    assert jl["flush"] == 1


def test_judge_and_save_missing_axis_keeps_report_and_earlier_scores(jl, monkeypatch):
    partial = {"analysis": "x", AXES[0]: 3}
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: dict(partial))
    pipeline_mod.judge_and_save("fjl", "p")
    assert _quality() == partial and jl["scores"] == [(AXES[0], 3)]


# ══ 트레이스 (Langfuse) ═════════════════════════════════════════════
class FakeObservation:
    id = "span-xyz"

    def __init__(self):
        self.outputs = []

    def update(self, **kw):
        self.outputs.append(kw["output"])


class FakeLangfuse:
    def __init__(self, trace_id="trace-123"):
        self.calls, self.obs, self.flushed, self.trace_id = [], FakeObservation(), 0, trace_id

    def start_as_current_observation(self, **kw):
        self.calls.append(kw)
        obs = self.obs

        class CM:
            def __enter__(self):
                return obs

            def __exit__(self, *a):
                return False
        return CM()

    def get_current_trace_id(self):
        if isinstance(self.trace_id, Exception):
            raise self.trace_id
        return self.trace_id

    def score_current_trace(self, **kw):
        pass

    def flush(self):
        self.flushed += 1


@pytest.fixture()
def lf(monkeypatch):
    fake = FakeLangfuse()
    monkeypatch.setattr(tracing, "_disabled", False, raising=False)
    monkeypatch.setattr(tracing, "_client", fake, raising=False)
    return fake


class FakeGraph:
    def __init__(self, out=None, before=None):
        self.out, self.before = out, before

    def invoke(self, state, config=None):
        if self.before:
            self.before()
        if isinstance(self.out, Exception):
            raise self.out
        return self.out


def _graph_out(**kw):
    return {"result_name": "f_p.jpg", "prompt_used": "p", "checks": [], "gate_passed": True,
            "bubbles": [{"what": "logo", "label": "logo"}], "item": "chair", "considered": [],
            "visual_similarity": 0.87, "item_similarity": 0.84, "gen_attempts": 1,
            "photo_check": {"valid": True, "reason": ""}, "mode": "composite",
            "composite_reason": "document", "photo_type": "document", "wear_level": "heavy",
            "watermark": "on_item", **kw}


def test_run_transform_traces_route_and_flushes(lf, monkeypatch):
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out()))

    out = pipeline_mod.run_transform("f", "p", defer_judge=True)

    assert lf.calls[0]["name"] == "transform" and lf.calls[0]["as_type"] == "span"
    assert out["trace_id"] == "trace-123" and out["trace_span_id"] == "span-xyz"
    assert lf.obs.outputs == [{
        "gate_passed": True, "bubbles": 1, "item": "chair",
        "visual_similarity": 0.87, "item_similarity": 0.84,
        "gen_attempts": 1, "photo_check": {"valid": True, "reason": ""},
        "mode": "composite", "composite_reason": "document",
        "photo_type": "document", "wear_level": "heavy",
        "detect_failed": False, "verify_failed": False,
    }]
    assert lf.flushed >= 1


def test_run_transform_trace_id_error_is_swallowed(lf, monkeypatch):
    lf.trace_id = RuntimeError("no ctx")
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out()))
    assert pipeline_mod.run_transform("f", "p", defer_judge=True)["trace_id"] is None


def test_run_transform_graph_exception_flushes_and_leaves_no_stale_report(monkeypatch):
    """그래프가 새 결과를 저장한 뒤 터져도 옛 점수가 새 결과에 붙어 남지 않는다 — 시작할 때 지운다."""
    storage.save("quality", "f_p.json", b'{"stale": true}')
    flushed = []
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(RuntimeError("fal down")))
    monkeypatch.setattr(pipeline_mod, "flush", lambda: flushed.append(1))
    with pytest.raises(RuntimeError):
        pipeline_mod.run_transform("f", "p")
    assert flushed == [1] and storage.load("quality", "f_p.json") is None


def test_run_transform_with_result_traces_and_flushes(lf, monkeypatch):
    storage.save("original", "f.png", ORIG_PNG)
    monkeypatch.setattr(pipeline_mod.detector, "analyze", lambda img: (_ for _ in ()).throw(TypeError()))
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", lambda *a, **k: 0.8)
    monkeypatch.setattr(pipeline_mod, "judge_and_save", lambda *a, **k: None)

    out = pipeline_mod.run_transform_with_result("f", "studio_white", GEN_PNG)

    assert lf.calls[0]["name"] == "transform_dev"
    assert out["gate_passed"] is False     # analyze 실패 — 확인할 기준이 없다
    assert lf.obs.outputs == [{"gate_passed": False, "bubbles": 0, "item": "object",
                               "visual_similarity": 0.8, "item_similarity": None}]
    assert lf.flushed >= 1


def test_judge_and_save_attaches_to_given_trace(lf, monkeypatch):
    storage.save("original", "fjl.png", ORIG_PNG)
    storage.save("result", "fjl_p.jpg", GEN_PNG)
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: dict(REPORT))

    pipeline_mod.judge_and_save("fjl", "p", trace_id="trace-9", parent_span_id="span-7")
    pipeline_mod.judge_and_save("fjl", "p", trace_id=None, parent_span_id="span-7")

    assert lf.calls[0]["trace_context"] == {"trace_id": "trace-9", "parent_span_id": "span-7"}
    assert "trace_context" not in lf.calls[1]


# ══ 옛 성적표 ═════════════════════════════════════════════════════
@pytest.mark.parametrize("mode", ["generate", "original"])
def test_stale_report_written_during_graph_is_cleared(monkeypatch, mode):
    """이전 요청의 백그라운드 채점이 그래프 도중에 옛 점수를 저장해도 남지 않는다 — 그래프 직후 한 번 더 지운다."""
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(
        _graph_out(mode=mode, result_name="frt_p.jpg"),
        before=lambda: storage.save("quality", "frt_p.json", b'{"stale": true}')))
    pipeline_mod.run_transform("frt", "p", defer_judge=True)
    assert storage.load("quality", "frt_p.json") is None


def test_stale_report_delete_failure_does_not_fail_transform(monkeypatch):
    def boom(kind, name):
        raise PermissionError("s3:DeleteObject denied")
    monkeypatch.setattr(pipeline_mod.storage, "delete", boom)
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out()))
    assert pipeline_mod.run_transform("f", "p", defer_judge=True)["judge_pending"] is True
