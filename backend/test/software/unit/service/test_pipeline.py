"""app.services.pipeline - 그래프 라우팅/노드 동작 + `run_transform`/
`run_transform_with_result` 트레이싱 투명성.

무거운 LangGraph/모델 호출(VLM/fal.ai/DINOv2)은 전부 가짜로 바꿔치기해서
실제 네트워크/모델 다운로드 없이 검증한다. `score_similarity`/가드는 실제
DINOv2 를 부르므로 embedder.cosine_similarity 를 반드시 mock 한다.
"""
import json
import types

import pytest

import app.core.tracing as tracing
import app.services.pipeline as pipeline_mod
from app.core.config import settings
from app.services.persistence import storage


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


@pytest.fixture(autouse=True)
def restore_pipeline_mode(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_mode", "real", raising=False)
    yield


@pytest.fixture(autouse=True)
def default_composite_first(monkeypatch):
    """생성 전 배경 교체 기준은 스펙 기본값으로 고정 (.env 영향 차단)."""
    monkeypatch.setattr(settings, "composite_first_min_texts", 12, raising=False)
    monkeypatch.setattr(settings, "composite_first_min_anchors", 0, raising=False)


@pytest.fixture(autouse=True)
def no_detect_sleep(monkeypatch):
    """detect 재시도 사이 time.sleep 을 기록만 한다 (테스트가 1초씩 멈추지 않게).
    전역 time 모듈은 건드리지 않고 pipeline 모듈의 time 이름만 바꿔 끼운다."""
    import time as _time
    sleeps = []
    monkeypatch.setattr(pipeline_mod, "time", types.SimpleNamespace(
        time=_time.time, sleep=lambda s: sleeps.append(s)))
    return sleeps


class FakeObservation:
    def __init__(self):
        self.update_calls = []
        self.id = "span-xyz"
    def update(self, **kw):
        self.update_calls.append(kw)


class FakeObservationCM:
    def __init__(self, obs):
        self._obs = obs
    def __enter__(self):
        return self._obs
    def __exit__(self, *a):
        return False


class FakeLangfuseClient:
    def __init__(self, trace_id="trace-123"):
        self.calls = []
        self.obs = FakeObservation()
        self.flushed = False
        self.trace_id = trace_id
    def start_as_current_observation(self, **kw):
        self.calls.append(kw)
        return FakeObservationCM(self.obs)
    def get_current_trace_id(self):
        if isinstance(self.trace_id, Exception):
            raise self.trace_id
        return self.trace_id
    def flush(self):
        self.flushed = True


class FakeGraph:
    """`pipeline.GRAPH.invoke(state)` 흉내 - 실제 그래프/모델 호출 없음."""
    def __init__(self, out):
        self._out = out
        self.invoked_with = None
    def invoke(self, state, config=None):
        self.invoked_with = dict(state)
        return self._out


def _graph_out(**kw):
    return {
        "result_name": "abc_preset.jpg",
        "prompt_used": "some prompt",
        "checks": [{"what": "얼룩", "preserved": True}],
        "bubbles": [{"what": "얼룩", "label": "얼룩"}],
        "gate_passed": True,
        "item": "chair",
        "considered": ["scratch"],
        "visual_similarity": 0.87,
        "gen_attempts": 1,
        "photo_check": {"valid": True, "reason": ""},
        **kw,
    }


def _expected(out, *, judge_pending=False, trace_id=None, trace_span_id=None):
    return {
        "result_name": out["result_name"],
        "prompt_used": out["prompt_used"],
        "checks": out["checks"],
        "bubbles": out["bubbles"],
        "gate_passed": out["gate_passed"],
        "item": out["item"],
        "considered": out["considered"],
        "visual_similarity": out["visual_similarity"],
        "gen_attempts": out.get("gen_attempts"),
        "photo_check": out.get("photo_check"),
        "status": out.get("status", "pass"),
        "guard_report": out.get("guard_report", []),
        "mode": out.get("mode", "generate"),
        "composite_reason": out.get("composite_reason"),
        "detect_failed": out.get("detect_failed", False),
        "judge_pending": judge_pending,
        "trace_id": trace_id,
        "trace_span_id": trace_span_id,
    }


def _enable_fake_langfuse(monkeypatch, **kw):
    fake_lf = FakeLangfuseClient(**kw)
    monkeypatch.setattr(tracing, "_disabled", False, raising=False)
    monkeypatch.setattr(tracing, "_client", fake_lf, raising=False)
    return fake_lf


# ── run_transform(): mock 파이프라인 모드 (지름길, 트레이싱 없음) ──
def test_run_transform_mock_mode_bypasses_graph_and_tracing(monkeypatch, make_png):
    monkeypatch.setattr(settings, "pipeline_mode", "mock", raising=False)

    def _poison_invoke(state):
        raise AssertionError("mock 모드는 그래프를 타면 안 된다")
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(None), raising=False)
    monkeypatch.setattr(pipeline_mod.GRAPH, "invoke", _poison_invoke)

    storage.save("original", "fid1.jpg", make_png())

    result = pipeline_mod.run_transform("fid1", "preset_a")

    assert result == {
        "result_name": "fid1_preset_a.jpg", "prompt_used": "PASS-THROUGH",
        "checks": [], "bubbles": [], "gate_passed": None,
        "item": "object", "considered": [],
        "status": "pass", "guard_report": [], "mode": "generate",
    }


def test_run_transform_mock_mode_missing_original_raises(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_mode", "mock", raising=False)
    with pytest.raises(FileNotFoundError):
        pipeline_mod.run_transform("ghost-file-id", "preset_a")


# ── run_transform(): real 모드, 트레이싱 비활성 (투명성) ──
def test_run_transform_real_mode_disabled_tracing_returns_graph_output_unchanged(monkeypatch):
    out = _graph_out()
    fake_graph = FakeGraph(out)
    monkeypatch.setattr(pipeline_mod, "GRAPH", fake_graph, raising=False)

    result = pipeline_mod.run_transform("fid2", "preset_b")

    assert result == _expected(out)
    # defer_judge 를 요청하지 않으면 초기 state 는 정확히 두 키뿐
    assert fake_graph.invoked_with == {"file_id": "fid2", "preset_key": "preset_b"}


def test_run_transform_real_mode_disabled_tracing_missing_visual_similarity_defaults_none(monkeypatch):
    out = _graph_out()
    del out["visual_similarity"]
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(out), raising=False)

    result = pipeline_mod.run_transform("fid2b", "preset_b")

    assert result["visual_similarity"] is None


def test_run_transform_passes_through_composite_reason_and_detect_failed(monkeypatch):
    out = _graph_out(mode="composite", composite_reason="text_heavy", detect_failed=True)
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(out), raising=False)
    result = pipeline_mod.run_transform("fid2c", "preset_b")
    assert result["composite_reason"] == "text_heavy"
    assert result["detect_failed"] is True
    assert result["mode"] == "composite"


# ── run_transform(defer_judge=...) ──
def test_run_transform_defer_judge_adds_flag_to_initial_state(monkeypatch):
    fake_graph = FakeGraph(_graph_out())
    monkeypatch.setattr(pipeline_mod, "GRAPH", fake_graph, raising=False)

    result = pipeline_mod.run_transform("fid-d", "preset_b", defer_judge=True)

    assert fake_graph.invoked_with == {"file_id": "fid-d", "preset_key": "preset_b",
                                       "defer_judge": True}
    assert result["judge_pending"] is True


def test_run_transform_defer_judge_false_explicit_keeps_state_minimal(monkeypatch):
    fake_graph = FakeGraph(_graph_out())
    monkeypatch.setattr(pipeline_mod, "GRAPH", fake_graph, raising=False)
    result = pipeline_mod.run_transform("fid-d", "preset_b", defer_judge=False)
    assert "defer_judge" not in fake_graph.invoked_with
    assert result["judge_pending"] is False


def test_run_transform_defer_judge_blocked_is_not_pending(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "GRAPH",
                        FakeGraph(_graph_out(status="blocked")), raising=False)
    result = pipeline_mod.run_transform("fid-d", "preset_b", defer_judge=True)
    assert result["judge_pending"] is False


def test_run_transform_defer_judge_missing_status_counts_as_pass(monkeypatch):
    out = _graph_out()
    assert "status" not in out
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(out), raising=False)
    assert pipeline_mod.run_transform("f", "p", defer_judge=True)["judge_pending"] is True


def test_run_transform_defer_judge_is_keyword_only():
    with pytest.raises(TypeError):
        pipeline_mod.run_transform("f", "p", True)


# ── run_transform(): real 모드, 트레이싱 활성 ──
def test_run_transform_real_mode_enabled_tracing_updates_obs_and_flushes(monkeypatch):
    fake_lf = _enable_fake_langfuse(monkeypatch, trace_id="trace-abc")

    out = _graph_out()
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(out), raising=False)

    result = pipeline_mod.run_transform("fid3", "preset_c")

    assert result == _expected(out, trace_id="trace-abc", trace_span_id="span-xyz")
    assert fake_lf.calls[0]["name"] == "transform"
    assert fake_lf.calls[0]["as_type"] == "span"
    assert fake_lf.obs.update_calls == [{"output": {
        "gate_passed": True, "bubbles": 1, "item": "chair",
        "visual_similarity": 0.87,
        "gen_attempts": 1, "photo_check": {"valid": True, "reason": ""},
        "mode": "generate", "composite_reason": None, "detect_failed": False,
    }}]
    assert fake_lf.flushed is True


def test_run_transform_trace_id_error_is_swallowed(monkeypatch):
    _enable_fake_langfuse(monkeypatch, trace_id=RuntimeError("no ctx"))
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out()), raising=False)
    assert pipeline_mod.run_transform("f", "p")["trace_id"] is None


def test_run_transform_obs_output_includes_composite_reason(monkeypatch):
    fake_lf = _enable_fake_langfuse(monkeypatch)
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(
        _graph_out(mode="composite", composite_reason="guard_failed")), raising=False)
    pipeline_mod.run_transform("f", "p")
    output = fake_lf.obs.update_calls[0]["output"]
    assert output["composite_reason"] == "guard_failed"
    assert output["mode"] == "composite"


# ── run_transform_with_result(): dev 그래프 (build(dev=True)) ──
def _patch_nodes(monkeypatch, checks, gate_passed, bubbles_out, visual_similarity=0.5,
                 detect_out=None):
    monkeypatch.setattr(pipeline_mod, "load", lambda s: {
        "original": b"ORIGINAL", "preset": {"prompt": "P", "name": "n", "bg_color": "#fff"},
    })
    monkeypatch.setattr(pipeline_mod, "classify_node", lambda s: {
        "item": "chair", "considered": ["scratch"],
    })
    monkeypatch.setattr(pipeline_mod, "detect", lambda s: detect_out or {
        "anchors": [{"category": "other", "what": "얼룩", "where": "앞면"}],
    })
    # DINOv2 실 모델 로드/네트워크 방지 - 반드시 mock
    monkeypatch.setattr(pipeline_mod, "score_similarity", lambda s: {
        "visual_similarity": visual_similarity,
    })
    monkeypatch.setattr(pipeline_mod, "verify", lambda s: {
        "checks": checks, "gate_passed": gate_passed,
    })
    monkeypatch.setattr(pipeline_mod, "save_inspect", lambda s: {})
    monkeypatch.setattr(pipeline_mod, "run_judge", lambda s: {})
    monkeypatch.setattr(pipeline_mod, "finalize", lambda s: {"bubbles": bubbles_out})


def test_run_transform_with_result_disabled_tracing_returns_expected_shape(monkeypatch, make_png):
    checks = [{"what": "얼룩", "preserved": True}]
    bubbles_out = [{"what": "얼룩", "label": "얼룩"}]
    _patch_nodes(monkeypatch, checks, True, bubbles_out, visual_similarity=0.91)

    result_bytes = make_png(color=(10, 20, 30))
    result = pipeline_mod.run_transform_with_result("fid4", "preset_d", result_bytes)

    assert result == {
        "result_name": "fid4_preset_d.jpg",
        "prompt_used": "TEST: provided result (generate skipped)",
        "checks": checks, "bubbles": bubbles_out, "gate_passed": True,
        "item": "chair", "considered": ["scratch"], "visual_similarity": 0.91,
        "detect_failed": False, "status": "pass",
    }
    assert storage.load("result", "fid4_preset_d.jpg") is not None


def test_run_transform_with_result_reports_detect_failed(monkeypatch, make_png):
    _patch_nodes(monkeypatch, [], False, [],
                 detect_out={"anchors": [], "detect_failed": True})
    result = pipeline_mod.run_transform_with_result("fid4b", "preset_d", make_png())
    assert result["detect_failed"] is True
    assert result["status"] == "pass"


def test_run_transform_with_result_enabled_tracing_updates_obs_and_flushes(monkeypatch, make_png):
    fake_lf = _enable_fake_langfuse(monkeypatch)

    checks = [{"what": "얼룩", "preserved": False}]
    bubbles_out = []
    _patch_nodes(monkeypatch, checks, False, bubbles_out, visual_similarity=None)

    result_bytes = make_png(color=(40, 50, 60))
    result = pipeline_mod.run_transform_with_result("fid5", "preset_e", result_bytes)

    assert result["gate_passed"] is False
    assert result["visual_similarity"] is None
    assert fake_lf.calls[0]["name"] == "transform_dev"
    assert fake_lf.calls[0]["as_type"] == "span"
    assert fake_lf.obs.update_calls == [{"output": {
        "gate_passed": False, "bubbles": 0, "item": "chair",
        "visual_similarity": None,
    }}]
    assert fake_lf.flushed is True


def test_dev_graph_replaces_generate_with_use_provided_and_has_no_loops():
    g = pipeline_mod.build(dev=True).get_graph()
    nodes = set(g.nodes)
    edges = {(e.source, e.target) for e in g.edges}
    assert "use_provided" in nodes
    for n in ("generate", "validate_result", "mark_gate_retry", "composite"):
        assert n not in nodes
    # 앞단은 운영 그래프와 같다
    assert {("classify", "read_text"), ("classify", "detect"),
            ("read_text", "plan"), ("detect", "plan")} <= edges
    assert {("plan", "use_provided"), ("use_provided", "score_similarity"),
            ("score_similarity", "verify"), ("verify", "save_inspect"),
            ("save_inspect", "run_judge"), ("run_judge", "finalize")} <= edges
    # 조건부 분기 없음 = 각 노드의 나가는 간선이 하나뿐
    outs = {}
    for s, t in edges:
        outs.setdefault(s, set()).add(t)
    assert all(len(v) == 1 for k, v in outs.items() if k not in ("classify",))


def test_prod_graph_has_no_use_provided():
    assert "use_provided" not in set(pipeline_mod.GRAPH.get_graph().nodes)


def test_run_transform_with_result_builds_graph_each_call(monkeypatch, make_png):
    """두 번째 호출 전에 노드를 바꿔 끼우면 그 함수가 쓰여야 한다 (빌드 캐시 없음)."""
    _patch_nodes(monkeypatch, [], None, [], visual_similarity=0.1)
    first = pipeline_mod.run_transform_with_result("fidx", "p", make_png())
    monkeypatch.setattr(pipeline_mod, "score_similarity", lambda s: {"visual_similarity": 0.2})
    second = pipeline_mod.run_transform_with_result("fidx", "p", make_png())
    assert first["visual_similarity"] == 0.1
    assert second["visual_similarity"] == 0.2


def test_dev_graph_real_nodes_detect_failed_gate_false_no_generate(monkeypatch, make_png):
    """실제 노드로 dev 그래프: detect 실패면 plan 이 composite_reason 을 달아도 dev
    그래프엔 분기가 없어 제공 이미지를 그대로 쓰고, verify 는 VLM 없이 gate False."""
    storage.save("original", "fid-dv.png", make_png())
    monkeypatch.setattr(pipeline_mod.detector, "classify",
                        lambda img: {"item": "cup", "considered": []})

    def fail(*a, **k):
        raise ValueError("no defects list")
    monkeypatch.setattr(pipeline_mod.detector, "detect_defects", fail)

    def poison(*a, **k):
        raise AssertionError("금지된 호출")
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate", poison)
    monkeypatch.setattr(pipeline_mod, "_generate_ai", poison)
    monkeypatch.setattr(pipeline_mod.compositor, "compose", poison)
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", lambda a, b: 0.8)
    monkeypatch.setattr(pipeline_mod.judge, "judge",
                        lambda o, r: {"fidelity": 1, "realism": 1, "trust": 1})

    provided = make_png(color=(1, 2, 3))
    out = pipeline_mod.run_transform_with_result("fid-dv", "studio_white", provided)

    assert out["detect_failed"] is True
    assert out["gate_passed"] is False
    assert out["visual_similarity"] == 0.8
    assert out["prompt_used"] == "TEST: provided result (generate skipped)"
    assert storage.load("quality", "fid-dv_studio_white.json") is not None   # 동기 채점
    ins = json.loads(storage.load("quality", "fid-dv_studio_white_inspect.json"))
    assert ins["composite_reason"] is None     # dev 그래프: plan 이 사유를 달지 않음
    assert ins["mode"] == "generate"


# ── 출력 가드 폴백 정책 (validate_result) ──
from app.services.quality.guards import GuardResult, run_output_guards as _ORIG_RUN_OUTPUT_GUARDS

ORIG_COLOR = (120, 90, 60)   # make_png 기본색
GEN_COLOR = (250, 250, 250)
COMP_COLOR = (9, 9, 9)


def _hard_fail():
    return GuardResult(name="ocr_match", passed=False, value=0.5,
                       threshold=0.95, severity="hard")


def _soft_fail():
    return GuardResult(name="dino_band", passed=False, value=0.6,
                       threshold=0.75, severity="soft")


def _dino_ok(value=0.88):
    return GuardResult(name="dino_band", passed=True, value=value,
                       threshold=0.75, severity="soft")


def _patch_graph_deps(monkeypatch, make_png, guard_side_effects):
    """guard_side_effects: generate 1회당 run_output_guards 가 돌려줄 리스트를
    순서대로. 반환: 호출 기록 dict."""
    storage.save("original", "fid-g.png", make_png())

    calls = {"gen_seeds": [], "judge": 0, "verify": 0, "compose": 0,
             "ocr": [], "cosine": 0, "detect_kw": []}

    def fake_generate_ai(original, preset, seed=None):
        calls["gen_seeds"].append(seed)
        return make_png(color=GEN_COLOR)

    guard_iter = iter(guard_side_effects)

    def fake_guards(orig, result, anchors, ocr_before, ocr_after):
        calls["ocr"].append((list(ocr_before), list(ocr_after)))
        return next(guard_iter)

    def fake_judge(orig, result):
        calls["judge"] += 1
        return {"analysis": "ok", "fidelity": 5, "realism": 5, "trust": 5}

    def fake_verify(img, anchors, item, considered):
        calls["verify"] += 1
        return [{"what": a["what"], "preserved": True} for a in anchors]

    def fake_detect(img, item, considered, **kw):
        calls["detect_kw"].append(kw)
        return [{"category": "other", "what": "얼룩", "where": "앞면"}]

    def fake_cosine(orig, result):
        calls["cosine"] += 1
        return 0.9

    def fake_compose(original, bg, box=None):
        calls["compose"] += 1
        return make_png(color=COMP_COLOR)

    monkeypatch.setattr(pipeline_mod.detector, "classify",
                        lambda img: {"item": "chair", "considered": []})
    monkeypatch.setattr(pipeline_mod.detector, "detect_defects", fake_detect)
    monkeypatch.setattr(pipeline_mod, "_generate_ai", fake_generate_ai)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", fake_guards)
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": True, "reason": ""})
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", fake_cosine)
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate", fake_verify)
    monkeypatch.setattr(pipeline_mod.judge, "judge", fake_judge)
    monkeypatch.setattr(pipeline_mod.compositor, "compose", fake_compose)
    return calls


def _compose_fails(monkeypatch, calls=None, exc=None):
    def boom(original, bg, box=None):
        if calls is not None:
            calls["compose"] += 1
        raise exc or ValueError("물건을 찾지 못함")
    monkeypatch.setattr(pipeline_mod.compositor, "compose", boom)


def _mean_color(image_bytes):
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return img.resize((1, 1)).getpixel((0, 0))


def _close(c, ref, tol=2):
    return all(abs(a - b) <= tol for a, b in zip(c, ref))


def test_pipeline_detect_called_with_strict(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    pipeline_mod.run_transform("fid-g", "studio_white")
    assert calls["detect_kw"] == [{"strict": True}]


def test_guard_hard_fail_twice_then_compose_fails_returns_blocked_with_original(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png,
                              [[_hard_fail()], [_hard_fail(), _soft_fail()]])
    _compose_fails(monkeypatch, calls)

    result = pipeline_mod.run_transform("fid-g", "studio_white")

    assert result["status"] == "blocked"
    assert result["mode"] == "composite_failed"
    assert result["composite_reason"] == "guard_failed"
    assert calls["compose"] == 1
    assert [g["name"] for g in result["guard_report"]] == ["ocr_match", "dino_band"]
    assert result["guard_report"][0] == {
        "name": "ocr_match", "passed": False, "value": 0.5,
        "threshold": 0.95, "severity": "hard"}
    assert result["photo_check"] is None
    # 변환 이미지는 내보내지 않는다 — 결과 슬롯엔 원본
    served = storage.load("result", result["result_name"])
    assert _close(_mean_color(served), ORIG_COLOR)
    # blocked 결과로 verify/judge(비용 드는 VLM)를 돌리지 않는다
    assert calls["verify"] == 0 and calls["judge"] == 0
    assert result["gate_passed"] is None
    assert storage.load("quality", "fid-g_studio_white.json") is None


def test_guard_hard_fail_twice_then_compose_succeeds_gives_composite(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], [_hard_fail()]])

    result = pipeline_mod.run_transform("fid-g", "studio_white")

    assert result["status"] == "pass"
    assert result["mode"] == "composite"
    assert result["composite_reason"] == "guard_failed"
    assert len(calls["gen_seeds"]) == 2 and calls["compose"] == 1
    # 가드 불합격으로 왔으면 어떤 가드가 걸렸는지 기록은 남긴다
    assert [g["name"] for g in result["guard_report"]] == ["ocr_match"]
    assert result["photo_check"] is None
    assert result["gate_passed"] is True and calls["verify"] == 1
    assert calls["judge"] == 1
    assert result["visual_similarity"] == 0.9    # 합성본 기준으로 다시 계산
    served = storage.load("result", result["result_name"])
    assert _close(_mean_color(served), COMP_COLOR)
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["guard_failed"] is True and ins["composite_reason"] == "guard_failed"
    assert ins["mode"] == "composite"


def test_guard_first_hard_fail_is_seed_retry_not_composite(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], []])
    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})
    assert calls["compose"] == 0
    assert out["status"] == "pass" and out.get("mode", "generate") == "generate"
    assert not out.get("guard_failed") and out.get("composite_reason") is None


def test_guard_seed_retry_happens_exactly_once(monkeypatch, make_png):
    """항상 hard fail 이어도 generate 는 최초 1회 + seed 재시도 1회 = 2회뿐."""
    calls = _patch_graph_deps(monkeypatch, make_png,
                              [[_hard_fail()], [_hard_fail()], [_hard_fail()]])
    _compose_fails(monkeypatch, calls)

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert len(calls["gen_seeds"]) == 2
    assert calls["gen_seeds"][0] is None
    assert isinstance(calls["gen_seeds"][1], int)
    assert out["guard_seed"] == calls["gen_seeds"][1]
    assert out["status"] == "blocked"


def test_guard_seed_retry_same_prompt(monkeypatch, make_png):
    """재시도는 '동일 파라미터' — 프롬프트가 바뀌면 안 된다."""
    prompts = []
    _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], []])

    def spy(original, preset, seed=None):
        prompts.append(preset["prompt"])
        return make_png(color=GEN_COLOR)
    monkeypatch.setattr(pipeline_mod, "_generate_ai", spy)

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert len(prompts) == 2 and prompts[0] == prompts[1]
    assert out["status"] == "pass"
    assert out["guard_report"] == []


def test_guard_soft_only_passes_with_report(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[_soft_fail()]])

    result = pipeline_mod.run_transform("fid-g", "studio_white")

    assert result["status"] == "pass"
    assert result["guard_report"] == [{
        "name": "dino_band", "passed": False, "value": 0.6,
        "threshold": 0.75, "severity": "soft"}]
    assert calls["gen_seeds"] == [None]
    assert calls["judge"] == 1
    # dino_band 값이 곧 유사도 — 다시 계산하지 않는다
    assert result["visual_similarity"] == 0.6 and calls["cosine"] == 0


def test_guard_report_written_to_inspect_json(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], [_hard_fail()]])
    _compose_fails(monkeypatch, calls)

    pipeline_mod.run_transform("fid-g", "studio_white")

    inspect = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert inspect["status"] == "blocked"
    assert inspect["mode"] == "composite_failed"
    assert inspect["guard_failed"] is True
    assert inspect["guard_report"][0]["name"] == "ocr_match"
    assert isinstance(inspect["guard_seed"], int)
    assert "물건을 찾지 못함" in inspect["composite_error"]


def test_guard_exception_propagates(monkeypatch, make_png):
    """가드 계산 실패는 fail-open 하지 않는다 — 예외 그대로 전파."""
    _patch_graph_deps(monkeypatch, make_png, [])

    def boom(*a, **kw):
        raise RuntimeError("embedder down")
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", boom)

    with pytest.raises(RuntimeError, match="embedder down"):
        pipeline_mod.run_transform("fid-g", "studio_white")


def test_guard_retry_raising_leaves_no_generated_image(monkeypatch, make_png):
    _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()]])

    def gen(original, preset, seed=None):
        if seed is not None:
            raise TimeoutError("fal timeout")
        return make_png(color=GEN_COLOR)
    monkeypatch.setattr(pipeline_mod, "_generate_ai", gen)

    with pytest.raises(TimeoutError):
        pipeline_mod.run_transform("fid-g", "studio_white")

    assert storage.load("result", "fid-g_studio_white.jpg") is None


def test_guard_retry_does_not_consume_photo_budget(monkeypatch, make_png):
    monkeypatch.setattr(settings, "max_generate_attempts", 2, raising=False)
    calls = _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], [], []])
    checks = iter([{"valid": False, "reason": "cropped"}, {"valid": True, "reason": ""}])
    monkeypatch.setattr(pipeline_mod.detector, "check_photo", lambda img: next(checks))

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    seeds = calls["gen_seeds"]
    assert len(seeds) == 3
    assert seeds[0] is None and isinstance(seeds[1], int) and seeds[2] is None
    assert out["gen_attempts"] == 2
    assert out["status"] == "pass"


def test_hard_fail_after_guard_retry_used_goes_to_composite_then_blocks(monkeypatch, make_png):
    """가드 재시도를 이미 쓴 뒤 photo 재생성에서 hard fail → 추가 재시도 없이
    배경 교체로, 그마저 실패하면 blocked."""
    monkeypatch.setattr(settings, "max_generate_attempts", 2, raising=False)
    calls = _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], [], [_hard_fail()]])
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": False, "reason": "cropped"})
    _compose_fails(monkeypatch, calls)

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert len(calls["gen_seeds"]) == 3
    assert calls["compose"] == 1
    assert out["status"] == "blocked"
    assert out["photo_check"] is None


def test_guard_anchors_only_boxed_passed(monkeypatch, make_png):
    seen = {}
    _patch_graph_deps(monkeypatch, make_png, [])
    boxed = {"type": "defect", "box": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}}
    monkeypatch.setattr(pipeline_mod.detector, "detect_defects",
                        lambda img, item, considered, **kw: [
                            {"category": "other", "what": "얼룩", "where": "앞면"}, boxed])

    def spy(orig, result, anchors, ocr_before, ocr_after):
        seen["anchors"] = anchors
        return []
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", spy)

    pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert seen["anchors"] == [boxed]


def test_real_guards_with_empty_ocr_pass(monkeypatch, make_png):
    """원본 글자가 없으면 OCR 은 [] vs [] — 실제 가드로도 hard fail 이 안 나야 한다."""
    calls = _patch_graph_deps(monkeypatch, make_png, [])
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", _ORIG_RUN_OUTPUT_GUARDS)

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert out["status"] == "pass"
    assert calls["gen_seeds"] == [None]
    # 실제 가드의 dino_band 가 계산한 값이 유사도로 그대로 쓰인다 (cosine 1회뿐)
    assert out["visual_similarity"] == 0.9 and calls["cosine"] == 1


def _text_reader(monkeypatch, original_texts, result_texts):
    """read_item_text: 원본(첫 호출)과 결과(이후 호출)에 다른 글자를 돌려준다."""
    seen = []

    def fake(img, item, **kw):
        seen.append({"img": img, "item": item, **kw})
        texts = original_texts if len(seen) == 1 else result_texts
        if isinstance(texts, Exception):
            raise texts
        return {"item_box": None, "texts": texts}
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text", fake)
    return seen


def test_real_guards_ocr_same_text_passes(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [])
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", _ORIG_RUN_OUTPUT_GUARDS)
    monkeypatch.setattr(settings, "text_lock", True)
    reads = _text_reader(monkeypatch, [{"text": "NIKE"}, {"text": "AIR"}],
                         [{"text": "AIR"}, {"text": "NIKE"}])   # 순서만 다름

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert out["status"] == "pass" and calls["gen_seeds"] == [None]
    assert len(reads) == 2
    assert reads[1]["strict"] is True and reads[1]["item"] == "chair"
    assert _close(_mean_color(reads[1]["img"]), GEN_COLOR)   # 결과 이미지를 읽음


def test_real_guards_ocr_garbled_text_triggers_seed_retry(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [])
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", _ORIG_RUN_OUTPUT_GUARDS)
    monkeypatch.setattr(settings, "text_lock", True)
    _text_reader(monkeypatch, [{"text": "시한부 선고"}], [{"text": "xq"}])
    _compose_fails(monkeypatch, calls)

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert len(calls["gen_seeds"]) == 2           # 최초 + seed 재시도
    assert out["status"] == "blocked"
    names = {g["name"] for g in out["guard_report"]}
    assert "ocr_match" in names and "no_added_text" in names


def test_ocr_read_failure_is_hard_fail_not_pass(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[], []])
    monkeypatch.setattr(settings, "text_lock", True)
    _text_reader(monkeypatch, [{"text": "SALE"}], RuntimeError("vlm down"))

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    # 결과 읽기 실패 → 두 번 다 hard fail → 배경 교체
    assert len(calls["gen_seeds"]) == 2
    assert out["mode"] == "composite" and out["composite_reason"] == "guard_failed"
    # 읽기 실패면 가드엔 빈 OCR 을 넘긴다 (별도 ocr_read_failed 로 막음)
    assert calls["ocr"] == [([], []), ([], [])]


def test_text_lock_puts_original_text_into_generate_prompt(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    monkeypatch.setattr(settings, "text_lock", True)
    _text_reader(monkeypatch,
                 [{"text": "시한부", "x1": 300, "y1": 100, "x2": 700, "y2": 200}],
                 [{"text": "시한부"}])
    prompts = []
    monkeypatch.setattr(pipeline_mod, "_generate_ai",
                        lambda original, preset, seed=None: prompts.append(preset["prompt"]) or make_png())

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert '"시한부" (top-center)' in prompts[0]
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["item_texts"][0]["text"] == "시한부"
    assert out["status"] == "pass"
    assert calls["ocr"] == [(["시한부"], ["시한부"])]


def test_text_lock_read_failure_generates_without_text(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    monkeypatch.setattr(settings, "text_lock", True)
    reads = []

    def boom(img, item, **kw):
        reads.append(kw)
        raise RuntimeError("vlm down")
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text", boom)
    prompts = []
    monkeypatch.setattr(pipeline_mod, "_generate_ai",
                        lambda original, preset, seed=None: prompts.append(preset["prompt"]) or make_png())

    out = pipeline_mod.run_transform("fid-g", "studio_white")
    assert "Text printed on the product" not in prompts[0]
    # 원본 글자가 없으니 결과는 읽지 않는다 (읽기 1회 = 원본뿐), OCR 은 빈 목록끼리
    assert len(reads) == 1
    assert calls["ocr"] == [([], [])]
    assert out["status"] == "pass"


# ── 게이트 실패 → 1회 재생성 → 배경 교체 모드 ──
def _verify_seq(monkeypatch, seq):
    """verify 가 호출될 때마다 seq 의 preserved 값을 차례로 돌려준다."""
    it = iter(seq)
    calls = []

    def fake_verify(img, anchors, item, considered):
        calls.append(img)
        return [{"what": "얼룩", "preserved": next(it)}]
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate", fake_verify)
    return calls


def test_gate_fail_once_regenerates_with_lost_defects_in_prompt(monkeypatch, make_png):
    _patch_graph_deps(monkeypatch, make_png, [[], []])
    _verify_seq(monkeypatch, [False, True])
    prompts = []
    monkeypatch.setattr(pipeline_mod, "_generate_ai",
                        lambda original, preset, seed=None: prompts.append(preset["prompt"]) or make_png())

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert len(prompts) == 2
    assert '"얼룩"' in prompts[1] and '"얼룩"' not in prompts[0]
    assert out["gate_passed"] is True and out.get("mode", "generate") == "generate"


def test_gate_fail_twice_switches_to_composite(monkeypatch, make_png):
    _patch_graph_deps(monkeypatch, make_png, [[], []])
    verify_calls = _verify_seq(monkeypatch, [False, False, True])
    composed = make_png(color=COMP_COLOR)
    monkeypatch.setattr(pipeline_mod.compositor, "compose", lambda original, bg, box=None: composed)

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert out["mode"] == "composite" and out["gate_passed"] is True
    assert out["composite_reason"] == "gate_failed"
    assert len(verify_calls) == 3
    saved = storage.load("result", "fid-g_studio_white.jpg")
    assert _mean_color(saved)[0] < 30
    assert _mean_color(verify_calls[-1])[0] < 30
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["mode"] == "composite" and ins["gate_retried"] is True
    assert ins["composite_reason"] == "gate_failed"


def test_composite_after_generate_recomputes_similarity(monkeypatch, make_png):
    """generate 때 dino_band 값(0.77)이 있어도 합성본으로 바뀌면 다시 계산해야 한다."""
    calls = _patch_graph_deps(monkeypatch, make_png, [[_dino_ok(0.77)], [_dino_ok(0.77)]])
    _verify_seq(monkeypatch, [False, False, True])

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert out["mode"] == "composite"
    assert out["visual_similarity"] == 0.9 and calls["cosine"] == 1


def test_generate_uses_dino_band_value_without_recompute(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[_dino_ok(0.77)]])
    out = pipeline_mod.run_transform("fid-g", "studio_white")
    assert out["visual_similarity"] == 0.77 and calls["cosine"] == 0


def test_composite_failure_keeps_generated_result_and_stops(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[], []])
    verify_calls = _verify_seq(monkeypatch, [False, False])
    _compose_fails(monkeypatch, calls)

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert out["mode"] == "composite_failed" and out["gate_passed"] is False
    assert out["status"] == "pass"                 # 가드 불합격이 아니면 blocked 아님
    assert len(verify_calls) == 2
    saved = storage.load("result", "fid-g_studio_white.jpg")
    assert _mean_color(saved)[0] > 200             # 생성본 유지


def test_worst_path_fits_recursion_limit(monkeypatch, make_png):
    """재생성 한도 5 + 가드 재시도 + 게이트 재생성 + 합성까지 다 타도 GraphRecursionError 없이 끝난다."""
    from dataclasses import dataclass
    _patch_graph_deps(monkeypatch, make_png, [])
    monkeypatch.setattr(settings, "max_generate_attempts", 5)

    @dataclass
    class G:
        name: str = "x"; passed: bool = False; value: float = 0; threshold: float = 1; severity: str = "hard"
    state = {"n": 0}

    def guards_first_fail_each_round(orig, result, anchors, a, b):
        state["n"] += 1
        return [G()] if state["n"] == 1 else []
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", guards_first_fail_each_round)
    monkeypatch.setattr(pipeline_mod.guards, "decide",
                        lambda results: ("block" if any(not g.passed for g in results) else "pass", None))
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": False, "reason": "cropped"})
    _verify_seq(monkeypatch, [False, False, False])

    out = pipeline_mod.run_transform("fid-g", "studio_white")
    assert out["mode"] == "composite"


def test_worst_path_with_plan_composite_failure_fits_recursion_limit(monkeypatch, make_png):
    """생성 전 합성(실패) → 생성 한도 5 + 가드 재시도 + 게이트 재생성 + 합성(실패)."""
    from dataclasses import dataclass
    calls = _patch_graph_deps(monkeypatch, make_png, [])
    monkeypatch.setattr(settings, "max_generate_attempts", 5)
    monkeypatch.setattr(settings, "composite_first_min_anchors", 1)   # plan → composite
    _compose_fails(monkeypatch, calls)

    @dataclass
    class G:
        name: str = "x"; passed: bool = False; value: float = 0; threshold: float = 1; severity: str = "hard"
    state = {"n": 0}

    def first_fails(orig, result, anchors, a, b):
        state["n"] += 1
        return [G()] if state["n"] == 1 else []
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", first_fails)
    monkeypatch.setattr(pipeline_mod.guards, "decide",
                        lambda results: ("block" if any(not g.passed for g in results) else "pass", None))
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": False, "reason": "cropped"})
    _verify_seq(monkeypatch, [False] * 5)

    out = pipeline_mod.run_transform("fid-g", "studio_white")
    assert out["mode"] == "composite_failed"
    assert calls["compose"] == 1        # 이미 실패한 오리기는 다시 부르지 않는다


# ══ detect 실패 ≠ "하자 없음" ═══════════════════════
def _detect_seq(monkeypatch, seq):
    """detect_defects 가 호출될 때마다 seq 항목을 차례로: Exception 이면 raise, 아니면 반환."""
    it = iter(seq)
    calls = []

    def fake(img, item, considered, **kw):
        calls.append({"item": item, **kw})
        v = next(it)
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(pipeline_mod.detector, "detect_defects", fake)
    return calls


def _no_vlm_verify(monkeypatch):
    def poison(*a, **k):
        raise AssertionError("verify VLM 호출 금지")
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate", poison)


def test_detect_both_attempts_fail_marks_detect_failed(monkeypatch, no_detect_sleep):
    calls = _detect_seq(monkeypatch, [ValueError("no defects"), RuntimeError("down")])
    out = pipeline_mod.detect({"original": b"x", "item": "chair", "considered": []})
    assert out == {"anchors": [], "detect_failed": True}
    assert len(calls) == pipeline_mod.DETECT_ATTEMPTS == 2
    assert all(c["strict"] is True for c in calls)
    # 시도 사이 1회만 쉰다 (마지막 실패 뒤엔 안 쉼)
    assert no_detect_sleep == [pipeline_mod.DETECT_RETRY_DELAY_S]


def test_detect_first_fail_then_success(monkeypatch, no_detect_sleep):
    anchors = [{"category": "other", "what": "얼룩", "where": "앞면"}]
    calls = _detect_seq(monkeypatch, [ValueError("empty"), anchors])
    out = pipeline_mod.detect({"original": b"x"})
    assert out == {"anchors": anchors, "detect_failed": False}
    assert [c["item"] for c in calls] == ["object", "object"]
    assert no_detect_sleep == [pipeline_mod.DETECT_RETRY_DELAY_S]


def test_detect_first_success_no_retry(monkeypatch, no_detect_sleep):
    calls = _detect_seq(monkeypatch, [[]])
    out = pipeline_mod.detect({"original": b"x", "item": "cup"})
    assert out == {"anchors": [], "detect_failed": False}   # 진짜 "하자 없음"
    assert len(calls) == 1 and calls[0]["strict"] is True
    assert no_detect_sleep == []


def test_detect_catches_non_value_errors(monkeypatch):
    _detect_seq(monkeypatch, [KeyError("x"), TimeoutError("t")])
    assert pipeline_mod.detect({"original": b"x"})["detect_failed"] is True


def test_verify_detect_failed_generate_mode_fails_gate_without_vlm(monkeypatch):
    _no_vlm_verify(monkeypatch)
    s = {"detect_failed": True, "anchors": [], "result_name": "r.jpg",
         "item_texts": [{"text": "HELLO"}]}      # 글자가 있어도 VLM 안 부름
    assert pipeline_mod.verify(s) == {"checks": [], "gate_passed": False}
    assert pipeline_mod.verify({**s, "mode": "generate"})["gate_passed"] is False
    assert pipeline_mod.verify({**s, "mode": "composite_failed"})["gate_passed"] is False


def test_verify_detect_failed_composite_mode_is_unverified(monkeypatch):
    _no_vlm_verify(monkeypatch)
    s = {"detect_failed": True, "anchors": [], "mode": "composite", "result_name": "r.jpg"}
    assert pipeline_mod.verify(s) == {"checks": [], "gate_passed": None}


def test_verify_blocked_wins_over_detect_failed(monkeypatch):
    _no_vlm_verify(monkeypatch)
    s = {"detect_failed": True, "status": "blocked", "anchors": []}
    assert pipeline_mod.verify(s) == {"checks": [], "gate_passed": None}


def test_verify_blocked_with_targets_no_call(monkeypatch):
    _no_vlm_verify(monkeypatch)
    s = {"status": "blocked", "anchors": [{"category": "other", "what": "a", "where": "b"}],
         "item_texts": [{"text": "AB"}]}
    assert pipeline_mod.verify(s) == {"checks": [], "gate_passed": None}


def test_verify_no_targets_skips_vlm_and_gate_none(monkeypatch):
    _no_vlm_verify(monkeypatch)
    s = {"anchors": [], "item_texts": [], "detect_failed": False}
    assert pipeline_mod.verify(s) == {"checks": [], "gate_passed": None}


def test_verify_composite_mode_excludes_text_targets(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate",
                        lambda img, t, i, c: seen.append(t) or
                        [{"what": x["what"], "preserved": True} for x in t])
    anchor = {"category": "other", "what": "얼룩", "where": "앞면"}
    out = pipeline_mod.verify({"anchors": [anchor], "item_texts": [{"text": "ABC"}],
                               "mode": "composite", "result_name": "r.jpg"})
    assert seen == [[anchor]] and out["gate_passed"] is True


def test_verify_composite_mode_texts_only_skips_vlm(monkeypatch):
    _no_vlm_verify(monkeypatch)
    out = pipeline_mod.verify({"anchors": [], "item_texts": [{"text": "ABC"}],
                               "mode": "composite", "result_name": "r.jpg"})
    assert out == {"checks": [], "gate_passed": None}


def test_verify_vlm_exception_leaves_gate_none(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("vlm down")
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate", boom)
    out = pipeline_mod.verify({"anchors": [{"category": "other", "what": "a", "where": "b"}],
                               "result_name": "r.jpg"})
    assert out == {"checks": [], "gate_passed": None}


@pytest.mark.parametrize("state,expected", [
    ({"gate_passed": False, "detect_failed": True}, "composite"),
    ({"gate_passed": False, "detect_failed": True, "gate_retried": False}, "composite"),
    ({"gate_passed": False, "detect_failed": False}, "regen"),
    ({"gate_passed": False, "detect_failed": False, "gate_retried": True}, "composite"),
    ({"gate_passed": None, "detect_failed": True, "mode": "composite"}, "done"),
    ({"gate_passed": False, "detect_failed": True, "mode": "composite"}, "done"),
    ({"gate_passed": False, "detect_failed": True, "mode": "composite_failed"}, "done"),
    ({"gate_passed": True, "detect_failed": True}, "done"),
    ({"gate_passed": None}, "done"),
])
def test_route_after_verify_detect_failed(state, expected):
    assert pipeline_mod._route_after_verify(state) == expected


@pytest.mark.parametrize("state,expected", [
    ({"guard_retry": True}, "retry"),
    ({"guard_retry": True, "guard_failed": True}, "retry"),
    ({"guard_failed": True}, "composite"),
    ({"guard_failed": True, "photo_check": {"valid": False}}, "composite"),
    ({"status": "pass", "photo_check": {"valid": True}}, "ok"),
    ({}, "ok"),
])
def test_route_after_validate(state, expected):
    assert pipeline_mod._route_after_validate(state) == expected


@pytest.mark.parametrize("mode,expected", [
    ("composite", "ok"), ("generate", "generate"),
    ("composite_failed", "failed"), (None, "failed"),
])
def test_route_after_composite(mode, expected):
    s = {} if mode is None else {"mode": mode}
    assert pipeline_mod._route_after_composite(s) == expected


def test_graph_detect_always_fails_goes_straight_to_composite(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[], []])
    detect_calls = _detect_seq(monkeypatch, [ValueError("x")] * 10)
    _no_vlm_verify(monkeypatch)

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert out["mode"] == "composite"
    assert out["composite_reason"] == "detect_failed"
    assert out["detect_failed"] is True
    assert out["gate_passed"] is None          # 합성본은 검증 불가 = None
    assert calls["gen_seeds"] == []            # 생성 0회 — plan 에서 바로 합성
    assert len(detect_calls) == 2
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["detect_failed"] is True and ins["anchors"] == []
    assert ins["mode"] == "composite" and ins["gate_retried"] is False
    assert ins["composite_reason"] == "detect_failed"
    saved = storage.load("result", "fid-g_studio_white.jpg")
    assert _mean_color(saved)[0] < 30


def test_graph_detect_fails_and_composite_fails_keeps_gate_false(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[], []])
    _detect_seq(monkeypatch, [ValueError("x")] * 10)
    _no_vlm_verify(monkeypatch)
    _compose_fails(monkeypatch, calls, ValueError("오리기 실패"))

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    # plan → composite(실패) → generate → verify(검증 불가 → False) → composite(재호출 없이 실패)
    assert out["mode"] == "composite_failed"
    assert out["gate_passed"] is False
    assert out["detect_failed"] is True
    assert out["status"] == "pass"
    assert out["composite_reason"] is None
    assert len(calls["gen_seeds"]) == 1
    assert calls["compose"] == 1
    saved = storage.load("result", "fid-g_studio_white.jpg")
    assert _mean_color(saved)[0] > 200         # 생성본이 남음


def test_graph_detect_retry_success_takes_normal_path(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    anchors = [{"category": "other", "what": "얼룩", "where": "앞면"}]
    detect_calls = _detect_seq(monkeypatch, [ValueError("empty"), anchors])

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert len(detect_calls) == 2
    assert out["detect_failed"] is False
    assert out["mode"] == "generate" and out["gate_passed"] is True
    assert out["composite_reason"] is None
    assert calls["verify"] == 1 and len(calls["gen_seeds"]) == 1
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["detect_failed"] is False and ins["anchors"] == anchors


def test_graph_detect_failed_then_guard_fails_twice_blocks(monkeypatch, make_png):
    """detect 실패 → 합성 실패 → 생성 → 가드 2회 불합격 → 합성(재호출 없이 실패) → blocked."""
    calls = _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], [_hard_fail()]])
    _detect_seq(monkeypatch, [ValueError("x")] * 10)
    _no_vlm_verify(monkeypatch)
    _compose_fails(monkeypatch, calls)

    out = pipeline_mod.run_transform("fid-g", "studio_white")
    assert out["status"] == "blocked" and out["gate_passed"] is None
    assert out["mode"] == "composite_failed"
    assert out["composite_reason"] == "guard_failed"
    assert calls["compose"] == 1 and len(calls["gen_seeds"]) == 2
    assert calls["judge"] == 0


# ══ plan: 생성 전 배경 교체 판단 ═════════════════════
def _texts(n):
    return [{"text": f"t{i:02d}"} for i in range(n)]


def _anchors(n):
    return [{"category": "other", "what": f"a{i}", "where": "x"} for i in range(n)]


@pytest.mark.parametrize("state,min_texts,min_anchors,expected", [
    ({}, 12, 0, None),
    ({"detect_failed": True}, 12, 0, "detect_failed"),
    ({"detect_failed": False, "item_texts": _texts(11)}, 12, 0, None),
    ({"item_texts": _texts(12)}, 12, 0, "text_heavy"),
    ({"item_texts": _texts(40)}, 12, 0, "text_heavy"),
    ({"item_texts": _texts(40)}, 0, 0, None),                  # 0 = 끔
    ({"item_texts": None}, 12, 0, None),
    ({"anchors": _anchors(50)}, 12, 0, None),                  # 기본 끔
    ({"anchors": _anchors(2)}, 12, 3, None),
    ({"anchors": _anchors(3)}, 12, 3, "many_defects"),
    ({"anchors": None}, 12, 1, None),
    # 우선순위: detect_failed > text_heavy > many_defects
    ({"detect_failed": True, "item_texts": _texts(12), "anchors": _anchors(3)}, 12, 3, "detect_failed"),
    ({"item_texts": _texts(12), "anchors": _anchors(3)}, 12, 3, "text_heavy"),
    ({"item_texts": _texts(1)}, 1, 0, "text_heavy"),
])
def test_composite_first_reason(monkeypatch, state, min_texts, min_anchors, expected):
    monkeypatch.setattr(settings, "composite_first_min_texts", min_texts)
    monkeypatch.setattr(settings, "composite_first_min_anchors", min_anchors)
    assert pipeline_mod._composite_first_reason(state) == expected


def test_composite_first_defaults_in_settings():
    from app.core.config import Settings
    f = Settings.model_fields
    assert f["composite_first_min_texts"].default == 12
    assert f["composite_first_min_anchors"].default == 0


def test_plan_sets_result_name_and_reason():
    out = pipeline_mod.plan({"file_id": "f", "preset_key": "p", "detect_failed": True})
    assert out == {"result_name": "f_p.jpg", "composite_reason": "detect_failed"}


def test_plan_no_reason_only_result_name():
    assert pipeline_mod.plan({"file_id": "f", "preset_key": "p"}) == {"result_name": "f_p.jpg"}


@pytest.mark.parametrize("provided", [b"img", b""])
def test_plan_dev_graph_never_sets_reason(provided):
    s = {"file_id": "f", "preset_key": "p", "detect_failed": True,
         "item_texts": _texts(50), "provided_result": provided}
    assert pipeline_mod.plan(s) == {"result_name": "f_p.jpg"}


@pytest.mark.parametrize("state,expected", [
    ({"composite_reason": "text_heavy"}, "composite"),
    ({"composite_reason": None}, "generate"),
    ({}, "generate"),
])
def test_route_after_plan(state, expected):
    assert pipeline_mod._route_after_plan(state) == expected


def test_graph_text_heavy_goes_to_composite_without_generate(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [])
    monkeypatch.setattr(settings, "text_lock", True)
    reads = _text_reader(monkeypatch, _texts(12), [])
    seen = []
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate",
                        lambda img, t, i, c: seen.append(t) or
                        [{"what": x["what"], "preserved": True} for x in t])

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert calls["gen_seeds"] == []
    assert out["mode"] == "composite" and out["composite_reason"] == "text_heavy"
    assert len(reads) == 1                      # 원본만 읽음 (OCR 가드 안 탐)
    assert [t["what"] for t in seen[0]] == ["얼룩"]   # 합성본은 글자 항목 없이 앵커만
    assert out["gate_passed"] is True
    assert out["visual_similarity"] == 0.9


def test_graph_many_defects_goes_to_composite(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [])
    monkeypatch.setattr(settings, "composite_first_min_anchors", 1)
    out = pipeline_mod.run_transform("fid-g", "studio_white")
    assert calls["gen_seeds"] == [] and calls["compose"] == 1
    assert out["composite_reason"] == "many_defects"


def test_graph_plan_composite_failure_falls_back_to_normal_generate(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    monkeypatch.setattr(settings, "text_lock", True)
    _text_reader(monkeypatch, _texts(12), _texts(12))
    _compose_fails(monkeypatch, calls)

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"},
                                    {"recursion_limit": pipeline_mod.RECURSION_LIMIT})

    assert calls["compose"] == 1
    assert calls["gen_seeds"] == [None]
    assert out["mode"] == "generate" and out["composite_reason"] is None
    assert out["composite_error"] == "물건을 찾지 못함"
    assert out["status"] == "pass" and out["gate_passed"] is True
    # OCR 가드 원본 글자는 _key_texts 로 고른 최대 TEXT_VERIFY_MAX 개
    assert len(calls["ocr"][0][0]) == pipeline_mod.TEXT_VERIFY_MAX


# ══ composite 노드 단위 ═══════════════════════════════
def _png(color):
    """storage.save 가 original/result 를 이미지로 정규화하므로 진짜 PNG 가 필요."""
    import io
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (32, 32), color).save(buf, format="PNG")
    return buf.getvalue()


ORIG_PNG = _png(ORIG_COLOR)
COMP_PNG = _png(COMP_COLOR)
GEN_PNG = _png(GEN_COLOR)


def _comp_state(**kw):
    return {"original": ORIG_PNG, "preset": {"bg_color": "#fff"}, "result_name": "c_p.jpg",
            "file_id": "c", "preset_key": "p", **kw}


def _compose_ok(monkeypatch, out=COMP_PNG):
    seen = []
    monkeypatch.setattr(pipeline_mod.compositor, "compose",
                        lambda o, bg, box=None: seen.append((o, bg, box)) or out)
    return seen


@pytest.mark.parametrize("state,expected", [
    ({}, "gate_failed"),
    ({"detect_failed": True}, "detect_failed"),
    ({"composite_reason": "text_heavy", "detect_failed": True}, "text_heavy"),
    ({"composite_reason": None, "detect_failed": False}, "gate_failed"),
])
def test_composite_reason_defaults(monkeypatch, state, expected):
    _compose_ok(monkeypatch)
    out = pipeline_mod.composite(_comp_state(**state))
    assert out["composite_reason"] == expected and out["mode"] == "composite"


def test_composite_success_resets_similarity_and_reports(monkeypatch):
    seen = _compose_ok(monkeypatch)
    out = pipeline_mod.composite(_comp_state(
        item_box={"x1": 1}, visual_similarity=0.77, photo_check={"valid": False},
        guard_report=[{"name": "dino_band"}]))
    assert seen == [(ORIG_PNG, "#fff", {"x1": 1})]
    assert out["visual_similarity"] is None and out["photo_check"] is None
    assert out["guard_report"] == [] and out["status"] == "pass"
    assert out["composite_error"] is None and out["result"] == COMP_PNG
    assert _close(_mean_color(storage.load("result", "c_p.jpg")), COMP_COLOR)


def test_composite_success_after_guard_failed_keeps_guard_report(monkeypatch):
    _compose_ok(monkeypatch)
    report = [{"name": "ocr_match", "passed": False}]
    out = pipeline_mod.composite(_comp_state(guard_failed=True, guard_report=report,
                                             composite_reason="guard_failed"))
    assert out["guard_report"] == report and out["status"] == "pass"


def test_composite_does_not_retry_after_previous_error(monkeypatch):
    def poison(*a, **k):
        raise AssertionError("이미 실패한 오리기를 다시 부르면 안 된다")
    monkeypatch.setattr(pipeline_mod.compositor, "compose", poison)
    out = pipeline_mod.composite(_comp_state(composite_error="old", result=b"GEN"))
    assert out["mode"] == "composite_failed"
    assert "old" in out["composite_error"]


def test_composite_previous_error_with_guard_failed_blocks(monkeypatch):
    monkeypatch.setattr(pipeline_mod.compositor, "compose",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")))
    out = pipeline_mod.composite(_comp_state(composite_error="old", result=GEN_PNG,
                                             guard_failed=True))
    assert out["status"] == "blocked" and out["result"] == ORIG_PNG
    assert out["mode"] == "composite_failed" and out["photo_check"] is None
    assert _close(_mean_color(storage.load("result", "c_p.jpg")), ORIG_COLOR)


def test_composite_failure_before_generate_returns_generate_mode(monkeypatch):
    _compose_fails(monkeypatch)
    out = pipeline_mod.composite(_comp_state(composite_reason="text_heavy"))
    assert out == {"mode": "generate", "composite_error": "물건을 찾지 못함",
                   "composite_reason": None}
    assert storage.load("result", "c_p.jpg") is None


def test_composite_failure_after_generate_keeps_result(monkeypatch):
    _compose_fails(monkeypatch)
    out = pipeline_mod.composite(_comp_state(result=b"GEN"))
    assert out == {"mode": "composite_failed", "composite_error": "물건을 찾지 못함"}


def test_composite_failure_guard_failed_blocks_even_before_result(monkeypatch):
    """guard_failed 가 result 유무보다 우선 (blocked 로 끝나야 함)."""
    _compose_fails(monkeypatch)
    out = pipeline_mod.composite(_comp_state(guard_failed=True))
    assert out["status"] == "blocked"


# ══ _key_texts / _verify_targets ═════════════════════
def _box(text, x1, y1, x2, y2):
    return {"text": text, "x1": x1, "y1": y1, "x2": x2, "y2": y2}


def test_key_texts_sorted_by_area_largest_first():
    texts = [_box("small", 0, 0, 10, 10), _box("big", 700, 700, 900, 900),
             {"text": "nobox"}, _box("mid", 0, 0, 100, 100)]
    assert pipeline_mod._key_texts(texts) == [
        ("big", "bottom-right"), ("mid", "top-left"), ("small", "top-left"), ("nobox", "")]


def test_key_texts_cap_keeps_largest():
    texts = [_box(f"t{i:02d}", 0, 0, i + 1, 1) for i in range(20)]   # 면적 = i+1
    out = pipeline_mod._key_texts(texts)
    assert len(out) == pipeline_mod.TEXT_VERIFY_MAX == 8
    assert [t for t, _ in out] == [f"t{i:02d}" for i in range(19, 11, -1)]


def test_key_texts_cap_counts_only_kept_items():
    """건너뛴 항목(1글자 등)은 상한에 세지 않는다."""
    texts = [{"text": "x"}] * 5 + [{"text": f"ok{i}"} for i in range(10)]
    assert len(pipeline_mod._key_texts(texts)) == 8


@pytest.mark.parametrize("text,kept", [
    ("", False), ("   ", False), ("\n\t", False), ("A", False), ("가", False),
    (" A ", False), ("AB", True), ("가나", True), (42, True), ("A\nB", True),
])
def test_key_texts_min_length(text, kept):
    out = pipeline_mod._key_texts([{"text": text}])
    assert (len(out) == 1) is kept


def test_key_texts_missing_text_key_skipped():
    assert pipeline_mod._key_texts([{}, {"x1": 0}]) == []


def test_key_texts_skips_raw_longer_than_lock_limit():
    from app.prompts.presets import TEXT_LOCK_MAX_CHARS
    exact = "a" * TEXT_LOCK_MAX_CHARS
    over = "b" * (TEXT_LOCK_MAX_CHARS + 1)
    padded = "  " + "c" * TEXT_LOCK_MAX_CHARS + "  "      # 앞뒤 공백은 길이에 안 셈
    out = pipeline_mod._key_texts([{"text": exact}, {"text": over}, {"text": padded}])
    assert [t for t, _ in out] == [exact, "c" * TEXT_LOCK_MAX_CHARS]


def test_key_texts_dedup_by_lowercase_and_where():
    texts = [_box("Brand", 0, 0, 100, 100), _box("BRAND", 0, 0, 90, 90),     # 같은 top-left
             _box("brand", 900, 900, 1000, 1000),                             # 다른 위치
             {"text": "Brand"}, {"text": "bRAND"}]                            # 위치 없음 끼리
    out = pipeline_mod._key_texts(texts)
    assert out == [("Brand", "top-left"), ("brand", "bottom-right"), ("Brand", "")]


def test_key_texts_covered_substring_skipped():
    out = pipeline_mod._key_texts([{"text": "NIKE"}, {"text": "Adidas"}],
                                  covered='printed logo "nike" on chest')
    assert out == [("Adidas", "")]


def test_key_texts_empty_covered_does_not_skip():
    assert pipeline_mod._key_texts([{"text": "AB"}], covered="") == [("AB", "")]


@pytest.mark.parametrize("texts", [None, []])
def test_key_texts_none_or_empty(texts):
    assert pipeline_mod._key_texts(texts) == []


def test_key_texts_sanitizes():
    assert pipeline_mod._key_texts([{"text": 'say "hi"\nnow'}]) == [("say 'hi' now", "")]


def test_verify_targets_merges_anchors_and_texts():
    anchors = [{"category": "other", "what": "얼룩", "where": "앞면"}]
    s = {"anchors": anchors, "item_texts": [
        {"text": "no-box"},
        _box("HELLO", 700, 700, 900, 900)]}
    targets = pipeline_mod._verify_targets(s)
    assert targets == anchors + [
        {"category": "print", "what": 'text: "HELLO"', "where": "bottom-right"},
        {"category": "print", "what": 'text: "no-box"', "where": "on the item"},
    ]
    assert s["anchors"] == anchors and len(anchors) == 1   # 원본 state 안 건드림


def test_verify_targets_dedup_against_print_anchor_case_insensitive():
    anchors = [{"category": "print", "what": 'Printed logo "NIKE" on chest', "where": "앞"}]
    s = {"anchors": anchors, "item_texts": [{"text": "nike"}, {"text": "Nike"}]}
    assert pipeline_mod._verify_targets(s) == anchors


def test_verify_targets_non_print_anchor_does_not_cover_text():
    anchors = [{"category": "stain", "what": 'stain over "NIKE" logo', "where": "앞"},
               {"what": "NIKE scuff", "where": "뒤"}]                    # category 없음
    s = {"anchors": anchors, "item_texts": [{"text": "NIKE"}]}
    assert [t["what"] for t in pipeline_mod._verify_targets(s)][-1] == 'text: "NIKE"'


def test_verify_targets_include_texts_false_returns_anchors_copy():
    anchors = [{"category": "other", "what": "얼룩", "where": "앞면"}]
    s = {"anchors": anchors, "item_texts": [{"text": "HELLO"}]}
    out = pipeline_mod._verify_targets(s, include_texts=False)
    assert out == anchors and out is not anchors


def test_verify_targets_uses_key_texts_rules():
    s = {"anchors": [], "item_texts": [{"text": "Brand"}, {"text": "BRAND"},
                                       {"text": "x"}, {"text": ""}]}
    assert [t["what"] for t in pipeline_mod._verify_targets(s)] == ['text: "Brand"']


def test_verify_targets_caps_text_lines():
    texts = [{"text": f"t{i:03d}"} for i in range(30)]
    targets = pipeline_mod._verify_targets({"anchors": [], "item_texts": texts})
    assert len(targets) == pipeline_mod.TEXT_VERIFY_MAX
    assert targets[-1]["what"] == f'text: "t{pipeline_mod.TEXT_VERIFY_MAX - 1:03d}"'


def test_verify_targets_cap_does_not_count_anchors():
    anchors = _anchors(10)
    targets = pipeline_mod._verify_targets({"anchors": anchors, "item_texts": _texts(20)})
    assert len(targets) == 10 + pipeline_mod.TEXT_VERIFY_MAX


def test_verify_targets_handles_missing_or_none_keys():
    assert pipeline_mod._verify_targets({}) == []
    assert pipeline_mod._verify_targets({"anchors": None, "item_texts": None}) == []


def test_verify_expected_counts_text_targets(monkeypatch):
    """앵커 1 + 글자 1 인데 verify 가 1개만 답하면 게이트 실패."""
    seen = {}

    def fake_verify(img, targets, item, considered):
        seen["targets"] = targets
        return [{"what": "얼룩", "preserved": True}]
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate", fake_verify)
    s = {"anchors": [{"category": "other", "what": "얼룩", "where": "앞면"}],
         "item_texts": [{"text": "ABC"}], "result_name": "none.jpg"}

    out = pipeline_mod.verify(s)

    assert len(seen["targets"]) == 2
    assert out["gate_passed"] is False


def test_verify_text_only_targets_still_gated(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate",
                        lambda img, t, i, c: calls.append(t) or
                        [{"what": x["what"], "preserved": True} for x in t])
    out = pipeline_mod.verify({"anchors": [], "item_texts": [{"text": "ABC"}],
                               "result_name": "none.jpg"})
    assert len(calls) == 1 and out["gate_passed"] is True


def test_lost_text_triggers_gate_retry_with_note(monkeypatch, make_png):
    _patch_graph_deps(monkeypatch, make_png, [[], []])
    monkeypatch.setattr(settings, "text_lock", True)
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text", lambda img, item, **kw: {
        "item_box": None, "texts": [{"text": "시한부"}]})
    rounds = iter([False, True])
    seen_targets = []

    def fake_verify(img, targets, item, considered):
        seen_targets.append(targets)
        ok = next(rounds)
        return [{"what": t["what"], "preserved": ok or t["category"] != "print"}
                for t in targets]
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate", fake_verify)
    prompts = []
    monkeypatch.setattr(pipeline_mod, "_generate_ai",
                        lambda original, preset, seed=None: prompts.append(preset["prompt"]) or make_png())

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert [t["what"] for t in seen_targets[0]] == ["얼룩", 'text: "시한부"']
    assert len(prompts) == 2
    note = prompts[1][len(prompts[0]):]
    assert "text: '시한부'" in note and "얼룩" not in note
    assert out["gate_retried"] is True and out["gate_passed"] is True
    assert out.get("mode", "generate") == "generate"


# ══ OCR 가드 입력 (_ocr_pair / _run_guards / validate_result) ══
def _poison_read(monkeypatch):
    def poison(*a, **k):
        raise AssertionError("결과 글자를 읽으면 안 된다")
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text", poison)


@pytest.mark.parametrize("texts", [None, [], [{"text": ""}], [{"text": "A"}, {"text": " "}],
                                   [{"text": "z" * 200}]])
def test_ocr_pair_no_key_texts_does_not_read_result(monkeypatch, texts):
    _poison_read(monkeypatch)
    s = {"result": b"R", "item_texts": texts}
    assert pipeline_mod._ocr_pair(s) == ([], [], [])


def test_ocr_pair_reads_result_with_strict(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text",
                        lambda img, item, **kw: seen.append((img, item, kw)) or {
                            "item_box": None,
                            "texts": [{"text": "NIKE"}, {"text": "x"}, {"text": "extra line"}]})
    s = {"result": b"R", "item": "shoe",
         "item_texts": [{"text": "NIKE"}, {"text": "A"}]}
    before, after, extra = pipeline_mod._ocr_pair(s)
    assert seen == [(b"R", "shoe", {"strict": True})]
    assert before == ["NIKE"]                      # _key_texts 로 고른 것만
    assert after == ["NIKE", "x", "extra line"]    # 결과는 읽은 전부
    assert extra == []


def test_ocr_pair_before_capped_like_verify(monkeypatch):
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text",
                        lambda img, item, **kw: {"item_box": None, "texts": []})
    before, _, _ = pipeline_mod._ocr_pair({"result": b"R", "item_texts": _texts(20)})
    assert len(before) == pipeline_mod.TEXT_VERIFY_MAX


def test_ocr_pair_default_item(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text",
                        lambda img, item, **kw: seen.append(item) or {"texts": []})
    pipeline_mod._ocr_pair({"result": b"R", "item_texts": [{"text": "AB"}]})
    assert seen == ["object"]


@pytest.mark.parametrize("exc", [ValueError("item_text: 응답에 texts 목록 없음"),
                                 RuntimeError("vlm down"), TimeoutError()])
def test_ocr_pair_read_failure_is_hard_guard(monkeypatch, exc):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text", boom)
    before, after, extra = pipeline_mod._ocr_pair({"result": b"R", "item_texts": [{"text": "AB"}]})
    assert before == [] and after == []
    assert extra == [GuardResult(name="ocr_read_failed", passed=False, value=0.0,
                                 threshold=1.0, severity="hard")]


def test_run_guards_returns_dino_value_and_passes_ocr(monkeypatch):
    seen = {}
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text",
                        lambda img, item, **kw: {"texts": [{"text": "AB"}]})

    def spy(orig, result, anchors, before, after):
        seen.update(before=before, after=after, anchors=anchors)
        return [_dino_ok(0.81)]
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", spy)

    verdict, report, dino = pipeline_mod._run_guards(
        {"original": b"O", "result": b"R", "item_texts": [{"text": "AB"}], "anchors": []})

    assert (verdict, report, dino) == ("pass", [], 0.81)
    assert seen == {"before": ["AB"], "after": ["AB"], "anchors": []}


def test_run_guards_no_dino_band_returns_none(monkeypatch):
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_soft_fail()][:0])
    assert pipeline_mod._run_guards({"original": b"O", "result": b"R"})[2] is None


def test_run_guards_ocr_read_failed_blocks_and_is_reported(monkeypatch):
    def boom(*a, **k):
        raise ValueError("no texts")
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text", boom)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_dino_ok(0.8)])
    verdict, report, dino = pipeline_mod._run_guards(
        {"original": b"O", "result": b"R", "item_texts": [{"text": "AB"}]})
    assert verdict == "block"
    assert [g["name"] for g in report] == ["ocr_read_failed"]
    assert dino == 0.8


def test_run_guards_real_guards_embedder_failure_gives_none(monkeypatch):
    """실제 가드: dino 계산 예외는 삼키고 dino_band 를 빼므로 dino=None."""
    def boom(*a):
        raise RuntimeError("OOM")
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", boom)
    verdict, report, dino = pipeline_mod._run_guards({"original": b"O", "result": b"R"})
    assert verdict == "pass" and report == [] and dino is None


def _validate_state(**kw):
    return {"original": ORIG_PNG, "result": GEN_PNG, "result_name": "v_p.jpg", **kw}


def test_validate_result_pass_returns_dino_similarity(monkeypatch):
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_dino_ok(0.83)])
    monkeypatch.setattr(pipeline_mod.detector, "check_photo", lambda img: {"valid": True, "reason": ""})
    out = pipeline_mod.validate_result(_validate_state())
    assert out["status"] == "pass" and out["visual_similarity"] == 0.83
    assert _close(_mean_color(storage.load("result", "v_p.jpg")), GEN_COLOR)


def test_validate_result_pass_without_dino_is_none(monkeypatch):
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])
    monkeypatch.setattr(pipeline_mod.detector, "check_photo", lambda img: {"valid": True, "reason": ""})
    assert pipeline_mod.validate_result(_validate_state())["visual_similarity"] is None


def test_validate_result_second_hard_fail_routes_to_composite_not_blocked(monkeypatch):
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_hard_fail()])
    out = pipeline_mod.validate_result(_validate_state(guard_seed=123))
    assert out["guard_failed"] is True and out["composite_reason"] == "guard_failed"
    assert out["guard_retry"] is False
    assert "status" not in out and "result" not in out
    assert storage.load("result", "v_p.jpg") is None      # 불합격본은 저장 안 함
    assert pipeline_mod._route_after_validate({**_validate_state(), **out}) == "composite"


def test_validate_result_first_hard_fail_seed_retry(monkeypatch):
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_hard_fail()])
    out = pipeline_mod.validate_result(_validate_state())
    assert out["guard_retry"] is True and isinstance(out["guard_seed"], int)
    assert "guard_failed" not in out


# ══ score_similarity / generate: 유사도 재사용·초기화 ══
def _score_spy(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline_mod, "score", lambda name, v, **kw: seen.append((name, v, kw)))
    return seen


def test_score_similarity_reuses_value_without_embedder(monkeypatch):
    seen = _score_spy(monkeypatch)

    def poison(*a):
        raise AssertionError("다시 계산 금지")
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", poison)
    out = pipeline_mod.score_similarity({"original": b"O", "result": b"R",
                                         "visual_similarity": 0.66})
    assert out == {}
    assert seen == [("visual_similarity", 0.66, {"data_type": "NUMERIC"})]


def test_score_similarity_zero_is_reused_not_recomputed(monkeypatch):
    """0.0 도 '계산된 값' — falsy 라고 다시 돌리면 안 된다."""
    _score_spy(monkeypatch)
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity",
                        lambda *a: (_ for _ in ()).throw(AssertionError("no")))
    assert pipeline_mod.score_similarity({"visual_similarity": 0.0}) == {}


def test_score_similarity_computes_when_missing(monkeypatch):
    seen = _score_spy(monkeypatch)
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", lambda o, r: 0.42)
    out = pipeline_mod.score_similarity({"original": b"O", "result": b"R",
                                         "visual_similarity": None})
    assert out == {"visual_similarity": 0.42}
    assert seen == [("visual_similarity", 0.42, {"data_type": "NUMERIC"})]


def test_score_similarity_blocked_is_none_even_with_value(monkeypatch):
    seen = _score_spy(monkeypatch)
    out = pipeline_mod.score_similarity({"status": "blocked", "visual_similarity": 0.9})
    assert out == {"visual_similarity": None} and seen == []


def test_score_similarity_embedder_failure_is_none(monkeypatch):
    _score_spy(monkeypatch)

    def boom(*a):
        raise RuntimeError("OOM")
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", boom)
    assert pipeline_mod.score_similarity({"original": b"O", "result": b"R"}) == {
        "visual_similarity": None}


def test_generate_resets_visual_similarity(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "_generate_ai", lambda o, p, seed=None: b"NEW")
    out = pipeline_mod.generate({"original": b"O", "preset": {"prompt": "P"},
                                 "file_id": "f", "preset_key": "p",
                                 "visual_similarity": 0.99})
    assert out["visual_similarity"] is None and out["result"] == b"NEW"


# ══ judge: 캐시 없음 + defer ═════════════════════════
def _judge_state(**kw):
    return {"file_id": "fj", "preset_key": "p", "original": b"O", "result": b"R", **kw}


def _seed_stale_quality(name="fj_p.json"):
    storage.save("quality", name, b'{"stale": true}')


def test_run_judge_rejudges_even_if_quality_file_exists(monkeypatch):
    _seed_stale_quality()
    calls = []
    monkeypatch.setattr(pipeline_mod.judge, "judge",
                        lambda o, r: calls.append(1) or {"fidelity": 4, "realism": 3, "trust": 2})
    pipeline_mod.run_judge(_judge_state())
    assert calls == [1]
    assert json.loads(storage.load("quality", "fj_p.json")) == {
        "fidelity": 4, "realism": 3, "trust": 2}


def test_run_judge_blocked_deletes_stale_and_skips_judge(monkeypatch):
    _seed_stale_quality()

    def poison(*a):
        raise AssertionError("blocked 에서 judge 금지")
    monkeypatch.setattr(pipeline_mod.judge, "judge", poison)
    assert pipeline_mod.run_judge(_judge_state(status="blocked")) == {}
    assert storage.load("quality", "fj_p.json") is None


def test_run_judge_defer_deletes_stale_and_skips_judge(monkeypatch):
    _seed_stale_quality()

    def poison(*a):
        raise AssertionError("defer 면 judge 금지")
    monkeypatch.setattr(pipeline_mod.judge, "judge", poison)
    assert pipeline_mod.run_judge(_judge_state(defer_judge=True)) == {}
    assert storage.load("quality", "fj_p.json") is None


def test_run_judge_defer_false_still_judges(monkeypatch):
    monkeypatch.setattr(pipeline_mod.judge, "judge",
                        lambda o, r: {"fidelity": 1, "realism": 1, "trust": 1})
    pipeline_mod.run_judge(_judge_state(defer_judge=False))
    assert storage.load("quality", "fj_p.json") is not None


def test_run_judge_delete_failure_is_swallowed(monkeypatch):
    def boom(kind, name):
        raise PermissionError("s3:DeleteObject denied")
    monkeypatch.setattr(pipeline_mod.storage, "delete", boom)
    monkeypatch.setattr(pipeline_mod.judge, "judge",
                        lambda o, r: {"fidelity": 2, "realism": 2, "trust": 2})
    pipeline_mod.run_judge(_judge_state())
    assert json.loads(storage.load("quality", "fj_p.json"))["fidelity"] == 2


def test_clear_quality_swallows_and_targets_quality_kind(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline_mod.storage, "delete", lambda k, n: seen.append((k, n)))
    pipeline_mod._clear_quality("a.json")
    assert seen == [("quality", "a.json")]

    def boom(k, n):
        raise RuntimeError("x")
    monkeypatch.setattr(pipeline_mod.storage, "delete", boom)
    pipeline_mod._clear_quality("a.json")        # 예외 없음


def test_run_judge_exception_leaves_no_stale_file(monkeypatch):
    _seed_stale_quality()

    def boom(*a):
        raise RuntimeError("judge down")
    monkeypatch.setattr(pipeline_mod.judge, "judge", boom)
    assert pipeline_mod.run_judge(_judge_state()) == {}
    assert storage.load("quality", "fj_p.json") is None


@pytest.mark.parametrize("empty", [None, {}])
def test_run_judge_empty_report_leaves_no_file(monkeypatch, empty):
    _seed_stale_quality()
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: empty)
    pipeline_mod.run_judge(_judge_state())
    assert storage.load("quality", "fj_p.json") is None


def test_run_judge_score_failure_still_saves_report(monkeypatch):
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: {"analysis": "partial"})
    pipeline_mod.run_judge(_judge_state())
    assert storage.load("quality", "fj_p.json") is not None


def test_run_judge_no_existing_file_ok(monkeypatch):
    monkeypatch.setattr(pipeline_mod.judge, "judge",
                        lambda o, r: {"fidelity": 1, "realism": 1, "trust": 1})
    pipeline_mod.run_judge(_judge_state())
    assert storage.load("quality", "fj_p.json") is not None


def test_mock_mode_deletes_stale_quality_file(monkeypatch, make_png):
    monkeypatch.setattr(settings, "pipeline_mode", "mock", raising=False)
    storage.save("original", "fid-m.jpg", make_png())
    storage.save("quality", "fid-m_preset_a.json", b'{"stale": true}')

    pipeline_mod.run_transform("fid-m", "preset_a")

    assert storage.load("quality", "fid-m_preset_a.json") is None


def test_transform_twice_rejudges_each_time_and_drops_stale(monkeypatch, make_png):
    _patch_graph_deps(monkeypatch, make_png, [[], []])
    storage.save("quality", "fid-g_studio_white.json", b'{"stale": true}')
    n = {"i": 0}

    def fake_judge(o, r):
        n["i"] += 1
        return {"analysis": f"run{n['i']}", "fidelity": n["i"], "realism": 5, "trust": 5}
    monkeypatch.setattr(pipeline_mod.judge, "judge", fake_judge)

    pipeline_mod.run_transform("fid-g", "studio_white")
    first = json.loads(storage.load("quality", "fid-g_studio_white.json"))
    pipeline_mod.run_transform("fid-g", "studio_white")
    second = json.loads(storage.load("quality", "fid-g_studio_white.json"))

    assert n["i"] == 2
    assert "stale" not in first and first["analysis"] == "run1"
    assert second["analysis"] == "run2" and second["fidelity"] == 2


def test_graph_defer_judge_skips_judge_and_marks_pending(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    storage.save("quality", "fid-g_studio_white.json", b'{"stale": true}')
    out = pipeline_mod.run_transform("fid-g", "studio_white", defer_judge=True)
    assert calls["judge"] == 0 and out["judge_pending"] is True
    assert storage.load("quality", "fid-g_studio_white.json") is None


# ══ judge_later (응답 뒤 백그라운드 채점) ═════════════
REPORT = {"analysis": "bg", "fidelity": 4, "realism": 3, "trust": 5}


@pytest.fixture()
def jl(monkeypatch):
    """judge_later 공통 준비: 원본/결과 저장 + flush/score 기록."""
    storage.save("original", "fjl.png", ORIG_PNG)
    storage.save("result", "fjl_p.jpg", GEN_PNG)
    rec = {"flush": 0, "scores": [], "judge_args": []}
    monkeypatch.setattr(pipeline_mod, "flush", lambda: rec.__setitem__("flush", rec["flush"] + 1))
    monkeypatch.setattr(pipeline_mod, "score",
                        lambda name, v, **kw: rec["scores"].append((name, v)))
    monkeypatch.setattr(pipeline_mod.judge, "judge",
                        lambda o, r: rec["judge_args"].append((o, r)) or dict(REPORT))
    return rec


def test_judge_later_saves_report_and_scores(jl):
    pipeline_mod.judge_later("fjl", "p")
    [(o, r)] = jl["judge_args"]
    assert _close(_mean_color(o), ORIG_COLOR) and _close(_mean_color(r), GEN_COLOR)
    assert json.loads(storage.load("quality", "fjl_p.json")) == REPORT
    from app.prompts.rubric import AXES
    assert jl["scores"] == [(a, REPORT[a]) for a in AXES]
    assert jl["flush"] == 1


@pytest.mark.parametrize("missing", ["original", "result"])
def test_judge_later_missing_input_returns_and_flushes(jl, missing, tmp_storage):
    if missing == "original":
        (tmp_storage / "original" / "fjl.png").unlink()
    else:
        (tmp_storage / "result" / "fjl_p.jpg").unlink()
    pipeline_mod.judge_later("fjl", "p")
    assert jl["judge_args"] == [] and jl["flush"] == 1
    assert storage.load("quality", "fjl_p.json") is None


@pytest.mark.parametrize("empty", [None, {}])
def test_judge_later_empty_report_saves_nothing(jl, monkeypatch, empty):
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: empty)
    pipeline_mod.judge_later("fjl", "p")
    assert storage.load("quality", "fjl_p.json") is None
    assert jl["scores"] == [] and jl["flush"] == 1


def test_judge_later_discards_when_result_changes_during_judge(jl, monkeypatch):
    def slow_judge(o, r):
        storage.save("result", "fjl_p.jpg", COMP_PNG)    # 채점 중 재변환
        return dict(REPORT)
    monkeypatch.setattr(pipeline_mod.judge, "judge", slow_judge)
    pipeline_mod.judge_later("fjl", "p")
    assert storage.load("quality", "fjl_p.json") is None
    assert jl["scores"] == [] and jl["flush"] == 1


def test_judge_later_result_changes_between_check_and_save_deletes_quality(jl, monkeypatch):
    real_save = storage.save

    def racing_save(kind, name, data):
        real_save(kind, name, data)
        if kind == "quality":
            real_save("result", "fjl_p.jpg", COMP_PNG)   # 저장 직후 재변환이 끼어듦
    monkeypatch.setattr(pipeline_mod.storage, "save", racing_save)

    pipeline_mod.judge_later("fjl", "p")

    assert storage.load("quality", "fjl_p.json") is None
    assert jl["scores"] == [] and jl["flush"] == 1


def test_judge_later_result_deleted_during_judge_discards(jl, monkeypatch, tmp_storage):
    def judge_then_delete(o, r):
        (tmp_storage / "result" / "fjl_p.jpg").unlink()
        return dict(REPORT)
    monkeypatch.setattr(pipeline_mod.judge, "judge", judge_then_delete)
    pipeline_mod.judge_later("fjl", "p")
    assert storage.load("quality", "fjl_p.json") is None


@pytest.mark.parametrize("where", ["judge", "load", "save", "score"])
def test_judge_later_swallows_exceptions_and_flushes(jl, monkeypatch, where):
    def boom(*a, **k):
        raise RuntimeError(where)
    target = {"judge": (pipeline_mod.judge, "judge"),
              "load": (pipeline_mod.storage, "load_original"),
              "save": (pipeline_mod.storage, "save"),
              "score": (pipeline_mod, "score")}[where]
    monkeypatch.setattr(*target, boom)
    pipeline_mod.judge_later("fjl", "p")        # 예외 없음
    assert jl["flush"] == 1


def test_judge_later_score_failure_keeps_saved_report(jl, monkeypatch):
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: {"analysis": "no axes"})
    pipeline_mod.judge_later("fjl", "p")
    assert storage.load("quality", "fjl_p.json") is not None and jl["flush"] == 1


def test_judge_later_attaches_to_given_trace_and_parent(monkeypatch):
    fake_lf = _enable_fake_langfuse(monkeypatch)
    storage.save("original", "fjl.png", ORIG_PNG)
    storage.save("result", "fjl_p.jpg", GEN_PNG)
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: dict(REPORT))

    pipeline_mod.judge_later("fjl", "p", "trace-9", "span-7")

    call = fake_lf.calls[0]
    assert call["name"] == "judge_async" and call["as_type"] == "span"
    assert call["trace_context"] == {"trace_id": "trace-9", "parent_span_id": "span-7"}
    assert call["input"] == {"file_id": "fjl", "preset_key": "p"}
    assert fake_lf.flushed is True


def test_judge_later_trace_without_parent(monkeypatch):
    fake_lf = _enable_fake_langfuse(monkeypatch)
    storage.save("original", "fjl.png", ORIG_PNG)
    storage.save("result", "fjl_p.jpg", GEN_PNG)
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: dict(REPORT))
    pipeline_mod.judge_later("fjl", "p", "trace-9")
    assert fake_lf.calls[0]["trace_context"] == {"trace_id": "trace-9"}


def test_judge_later_no_trace_id_no_trace_context(monkeypatch):
    fake_lf = _enable_fake_langfuse(monkeypatch)
    storage.save("original", "fjl.png", ORIG_PNG)
    storage.save("result", "fjl_p.jpg", GEN_PNG)
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: dict(REPORT))
    pipeline_mod.judge_later("fjl", "p", None, "span-7")
    assert "trace_context" not in fake_lf.calls[0]


# ══ 그래프 연결 ═════════════════════════════════════
def test_read_text_and_detect_both_finish_before_single_generate(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    monkeypatch.setattr(settings, "text_lock", True)
    order = []
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text",
                        lambda img, item, **kw: order.append("read_result" if kw.get("strict") else "read") or {
                            "item_box": None,
                            "texts": [{"text": "SALE", "x1": 0, "y1": 0, "x2": 100, "y2": 100}]})
    anchors = [{"category": "scratch", "what": "긁힘", "where": "뒷면"}]
    monkeypatch.setattr(pipeline_mod.detector, "detect_defects",
                        lambda img, item, considered, **kw: order.append("detect") or anchors)
    prompts = []

    def gen(original, preset, seed=None):
        order.append("gen")
        prompts.append(preset["prompt"])
        return make_png()
    monkeypatch.setattr(pipeline_mod, "_generate_ai", gen)

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert sorted(order[:2]) == ["detect", "read"]
    assert order[2:] == ["gen", "read_result"]     # 생성 1회 뒤 OCR 가드용 결과 읽기
    assert '"SALE" (top-left)' in prompts[0]
    assert calls["ocr"] == [(["SALE"], ["SALE"])]
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["anchors"] == anchors
    assert ins["item_texts"][0]["text"] == "SALE"
    assert out["status"] == "pass" and out["gate_passed"] is True


def test_graph_edges_read_text_and_detect_join_at_plan():
    g = pipeline_mod.GRAPH.get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    assert ("classify", "read_text") in edges
    assert ("classify", "detect") in edges
    assert ("read_text", "detect") not in edges
    assert ("read_text", "plan") in edges and ("detect", "plan") in edges
    assert ("plan", "generate") in edges and ("plan", "composite") in edges
    assert ("read_text", "generate") not in edges and ("detect", "generate") not in edges
    assert ("validate_result", "composite") in edges
    assert ("composite", "generate") in edges
    assert ("composite", "score_similarity") in edges and ("composite", "save_inspect") in edges


def test_key_texts_short_text_not_dropped_by_substring_of_print_anchor():
    """짧은 글자("on")가 print 앵커 문장("print on chest")의 부분 문자열이라고
    검증에서 빠지면 안 된다 — 3글자 이상 + 단어 단위로만 중복 처리."""
    texts = [{"text": "ON", "x1": 0, "y1": 0, "x2": 500, "y2": 500},
             {"text": "NIKE", "x1": 0, "y1": 0, "x2": 400, "y2": 400},
             {"text": "IKE", "x1": 0, "y1": 0, "x2": 300, "y2": 300}]
    covered = 'logo: "nike" print on chest'
    got = [t for t, _ in pipeline_mod._key_texts(texts, covered=covered)]
    assert "ON" in got          # 2글자 — 중복 판단 대상 아님
    assert "NIKE" not in got    # 단어로 들어 있음 → 중복
    assert "IKE" in got         # "nike" 의 일부일 뿐 단어 아님
