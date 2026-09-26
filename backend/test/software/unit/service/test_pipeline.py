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
        time=_time.time, monotonic=_time.monotonic, sleep=lambda s: sleeps.append(s)))
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
        "item_similarity": 0.84,
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
        "item_similarity": out.get("item_similarity"),
        "gen_attempts": out.get("gen_attempts"),
        "photo_check": out.get("photo_check"),
        "status": out.get("status", "pass"),
        "guard_report": out.get("guard_report", []),
        "mode": out.get("mode", "generate"),
        "composite_reason": out.get("composite_reason"),
        "detect_failed": out.get("detect_failed", False),
        "verify_failed": out.get("verify_failed", False),
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


def test_run_transform_missing_item_similarity_defaults_none(monkeypatch):
    out = _graph_out()
    del out["item_similarity"]
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(out), raising=False)
    assert pipeline_mod.run_transform("fid2p", "preset_b")["item_similarity"] is None


def test_run_transform_obs_output_missing_item_similarity_is_none(monkeypatch):
    fake_lf = _enable_fake_langfuse(monkeypatch)
    out = _graph_out()
    del out["item_similarity"]
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(out), raising=False)
    pipeline_mod.run_transform("f", "p")
    assert fake_lf.obs.update_calls[0]["output"]["item_similarity"] is None


def test_run_transform_passes_through_composite_reason_and_detect_failed(monkeypatch):
    out = _graph_out(mode="composite", composite_reason="text_heavy", detect_failed=True)
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(out), raising=False)
    result = pipeline_mod.run_transform("fid2c", "preset_b")
    assert result["composite_reason"] == "text_heavy"
    assert result["detect_failed"] is True
    assert result["mode"] == "composite"


# ── run_transform(defer_judge=...) ──
@pytest.mark.parametrize("defer", [True, False])
def test_run_transform_initial_state_never_carries_defer_judge(monkeypatch, defer):
    """defer_judge 는 State 에서 빠졌다 — 그래프 초기 state 는 모드와 무관하게 두 키뿐."""
    fake_graph = FakeGraph(_graph_out())
    monkeypatch.setattr(pipeline_mod, "GRAPH", fake_graph, raising=False)
    monkeypatch.setattr(pipeline_mod, "judge_and_save", lambda f, p, **kw: None)

    result = pipeline_mod.run_transform("fid-d", "preset_b", defer_judge=defer)

    assert fake_graph.invoked_with == {"file_id": "fid-d", "preset_key": "preset_b"}
    assert result["judge_pending"] is defer


def test_state_has_no_defer_judge_key():
    assert "defer_judge" not in pipeline_mod.State.__annotations__


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


class OrderedGraph(FakeGraph):
    """invoke 시점을 공용 순서 기록에 남긴다."""
    def __init__(self, out, order):
        super().__init__(out)
        self._order = order
    def invoke(self, state, config=None):
        self._order.append("graph")
        return super().invoke(state, config)


def _order_spies(monkeypatch, out):
    """clear/graph/judge_and_save 호출 순서를 한 리스트에 기록."""
    order = []
    monkeypatch.setattr(pipeline_mod, "GRAPH", OrderedGraph(out, order), raising=False)
    monkeypatch.setattr(pipeline_mod, "_clear_quality",
                        lambda name: order.append(("clear", name)))
    monkeypatch.setattr(pipeline_mod, "judge_and_save",
                        lambda f, p, **kw: order.append(("judge", f, p, kw)))
    return order


def test_run_transform_inline_judges_exactly_once_after_graph(monkeypatch):
    order = _order_spies(monkeypatch, _graph_out())
    result = pipeline_mod.run_transform("fo", "p")
    # 시작 시 삭제 → 그래프 → 그래프 직후 한 번 더 삭제 → 채점 1회 (트레이스 인자 없이)
    assert order == [("clear", "fo_p.json"), "graph", ("clear", "fo_p.json"),
                     ("judge", "fo", "p", {})]
    assert result["judge_pending"] is False


def test_run_transform_inline_missing_status_still_judges(monkeypatch):
    out = _graph_out()
    assert "status" not in out
    order = _order_spies(monkeypatch, out)
    pipeline_mod.run_transform("fo", "p", defer_judge=False)
    assert [o for o in order if o[0] == "judge"] == [("judge", "fo", "p", {})]


def test_run_transform_defer_never_calls_judge(monkeypatch):
    order = _order_spies(monkeypatch, _graph_out())

    def poison(*a, **k):
        raise AssertionError("defer 면 judge 금지")
    monkeypatch.setattr(pipeline_mod.judge, "judge", poison)
    result = pipeline_mod.run_transform("fo", "p", defer_judge=True)
    assert order == [("clear", "fo_p.json"), "graph", ("clear", "fo_p.json")]
    assert result["judge_pending"] is True


@pytest.mark.parametrize("defer", [True, False])
def test_run_transform_blocked_never_judges(monkeypatch, defer):
    order = _order_spies(monkeypatch, _graph_out(status="blocked"))

    def poison(*a, **k):
        raise AssertionError("blocked 에서 judge 금지")
    monkeypatch.setattr(pipeline_mod.judge, "judge", poison)
    result = pipeline_mod.run_transform("fo", "p", defer_judge=defer)
    assert not any(o[0] == "judge" for o in order if isinstance(o, tuple))
    assert result["judge_pending"] is False
    assert result["status"] == "blocked"


def test_run_transform_graph_exception_no_judge_but_flushes(monkeypatch):
    order = []
    flushed = []

    class BoomGraph:
        def invoke(self, state, config=None):
            order.append("graph")
            raise RuntimeError("fal down")
    monkeypatch.setattr(pipeline_mod, "GRAPH", BoomGraph(), raising=False)
    monkeypatch.setattr(pipeline_mod, "_clear_quality", lambda n: order.append(("clear", n)))
    monkeypatch.setattr(pipeline_mod, "judge_and_save",
                        lambda *a, **k: order.append(("judge",)))
    monkeypatch.setattr(pipeline_mod, "flush", lambda: flushed.append(1))
    with pytest.raises(RuntimeError):
        pipeline_mod.run_transform("fo", "p")
    assert order == [("clear", "fo_p.json"), "graph"]
    assert flushed == [1]


def test_mock_mode_never_judges(monkeypatch, make_png):
    monkeypatch.setattr(settings, "pipeline_mode", "mock", raising=False)
    storage.save("original", "fid-mj.jpg", make_png())

    def poison(*a, **k):
        raise AssertionError("mock 모드는 채점 안 함")
    monkeypatch.setattr(pipeline_mod, "judge_and_save", poison)
    monkeypatch.setattr(pipeline_mod.judge, "judge", poison)
    for defer in (False, True):
        out = pipeline_mod.run_transform("fid-mj", "preset_a", defer_judge=defer)
        assert "judge_pending" not in out


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
        "visual_similarity": 0.87, "item_similarity": 0.84,
        "gen_attempts": 1, "photo_check": {"valid": True, "reason": ""},
        "mode": "generate", "composite_reason": None, "detect_failed": False,
        "verify_failed": False,
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
    judged = []
    monkeypatch.setattr(pipeline_mod, "judge_and_save",
                        lambda f, p, **kw: judged.append((f, p, kw)))
    monkeypatch.setattr(pipeline_mod, "finalize", lambda s: {"bubbles": bubbles_out})
    return judged


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
        "item_similarity": None,   # dev 그래프엔 validate_result 가 없다
        "detect_failed": False, "verify_failed": False, "mode": "generate",
        "status": "pass",
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
        "visual_similarity": None, "item_similarity": None,
    }}]
    assert fake_lf.flushed is True


def test_dev_graph_replaces_generate_with_use_provided_and_has_no_loops():
    g = pipeline_mod.build(dev=True).get_graph()
    nodes = set(g.nodes)
    edges = {(e.source, e.target) for e in g.edges}
    assert "use_provided" in nodes
    for n in ("generate", "validate_result", "mark_gate_retry", "composite"):
        assert n not in nodes
    # 앞단은 운영 그래프와 같다: detect → plan → (필요할 때만) read_text
    assert {("classify", "detect"), ("detect", "plan"),
            ("plan", "read_text"), ("read_text", "use_provided")} <= edges
    assert ("classify", "read_text") not in edges and ("read_text", "plan") not in edges
    assert {("plan", "use_provided"), ("use_provided", "score_similarity"),
            ("score_similarity", "verify"), ("verify", "save_inspect"),
            ("save_inspect", "finalize")} <= edges
    # 갈림길은 plan(글자 읽기 여부) 하나뿐 — 재생성·배경 교체 루프 없음
    outs = {}
    for s, t in edges:
        outs.setdefault(s, set()).add(t)
    assert outs["plan"] == {"read_text", "use_provided"}
    assert all(len(v) == 1 for k, v in outs.items() if k != "plan")


def test_prod_graph_has_no_use_provided():
    assert "use_provided" not in set(pipeline_mod.GRAPH.get_graph().nodes)


@pytest.mark.parametrize("graph", [pipeline_mod.GRAPH, pipeline_mod.build(),
                                   pipeline_mod.build(dev=True)],
                         ids=["GRAPH", "build", "build_dev"])
def test_graphs_have_no_judge_node_and_save_inspect_goes_to_finalize(graph):
    """채점은 그래프 밖(judge_and_save) 한 곳뿐 — 그래프 안에 judge 노드가 없어야 한다."""
    g = graph.get_graph()
    nodes = set(g.nodes)
    assert "run_judge" not in nodes
    assert not any("judge" in n for n in nodes)
    edges = {(e.source, e.target) for e in g.edges}
    assert [t for s_, t in edges if s_ == "save_inspect"] == ["finalize"]
    assert ("finalize", "__end__") in edges


def test_removed_judge_entrypoints_are_gone():
    assert not hasattr(pipeline_mod, "run_judge")
    assert not hasattr(pipeline_mod, "judge_later")


def test_run_transform_with_result_judges_once_after_graph(monkeypatch, make_png):
    order = []
    _patch_nodes(monkeypatch, [], True, [])
    monkeypatch.setattr(pipeline_mod, "finalize",
                        lambda s: order.append("graph_end") or {"bubbles": []})
    monkeypatch.setattr(pipeline_mod, "_clear_quality", lambda n: order.append(("clear", n)))
    monkeypatch.setattr(pipeline_mod, "judge_and_save",
                        lambda f, p, **kw: order.append(("judge", f, p, kw)))
    pipeline_mod.run_transform_with_result("fw", "p", make_png())
    assert order == [("clear", "fw_p.json"), "graph_end", ("clear", "fw_p.json"),
                     ("judge", "fw", "p", {})]


def test_run_transform_with_result_judges_even_when_detect_failed(monkeypatch, make_png):
    judged = _patch_nodes(monkeypatch, [], False, [],
                          detect_out={"anchors": [], "detect_failed": True})
    pipeline_mod.run_transform_with_result("fw", "p", make_png())
    assert judged == [("fw", "p", {})]


def test_run_transform_with_result_clears_stale_quality(monkeypatch, make_png):
    _patch_nodes(monkeypatch, [], True, [])
    storage.save("quality", "fw_p.json", b'{"stale": true}')
    pipeline_mod.run_transform_with_result("fw", "p", make_png())
    assert storage.load("quality", "fw_p.json") is None


def test_run_transform_with_result_graph_exception_no_judge(monkeypatch, make_png):
    judged = _patch_nodes(monkeypatch, [], True, [])

    def boom(s):
        raise RuntimeError("verify down")
    monkeypatch.setattr(pipeline_mod, "verify", boom)
    with pytest.raises(RuntimeError):
        pipeline_mod.run_transform_with_result("fw", "p", make_png())
    assert judged == []


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
    monkeypatch.setattr(pipeline_mod.detector, "detect_full", fail)

    def poison(*a, **k):
        raise AssertionError("금지된 호출")
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate", poison)
    monkeypatch.setattr(pipeline_mod, "_generate_ai", poison)
    monkeypatch.setattr(pipeline_mod.compositor, "compose", poison)
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", lambda a, b: 0.8)
    judge_calls = []
    monkeypatch.setattr(pipeline_mod.judge, "judge",
                        lambda o, r: judge_calls.append(1) or
                        {"fidelity": 1, "realism": 1, "trust": 1})

    provided = make_png(color=(1, 2, 3))
    out = pipeline_mod.run_transform_with_result("fid-dv", "studio_white", provided)
    assert judge_calls == [1]      # detect 실패여도 dev 경로는 채점 1회

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
from app.services.ai.detector import verify_and_locate as _ORIG_VERIFY_AND_LOCATE

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


def _item_ok(value=0.86):
    return GuardResult(name="item_dino", passed=value >= 0.80, value=value,
                       threshold=0.80, severity="soft")


def _patch_graph_deps(monkeypatch, make_png, guard_side_effects, *,
                      text_level="simple", item_box=None):
    """guard_side_effects: generate 1회당 run_output_guards 가 돌려줄 리스트를
    순서대로. detect_full 은 앵커 1개 + text_level(기본 simple = 예전처럼 글자 읽기 후
    생성) + item_box 를 돌려준다. 반환: 호출 기록 dict."""
    storage.save("original", "fid-g.png", make_png())

    calls = {"gen_seeds": [], "judge": 0, "verify": 0, "compose": 0,
             "ocr": [], "cosine": 0, "detect_kw": []}

    def fake_generate_ai(original, preset, seed=None):
        calls["gen_seeds"].append(seed)
        return make_png(color=GEN_COLOR)

    guard_iter = iter(guard_side_effects)

    def fake_guards(orig, result, ocr_before, ocr_after):
        calls["ocr"].append((list(ocr_before), list(ocr_after)))
        return next(guard_iter)

    def fake_judge(orig, result):
        calls["judge"] += 1
        return {"analysis": "ok", "fidelity": 5, "realism": 5, "trust": 5}

    def fake_verify(img, anchors, item, considered, **kw):
        calls["verify"] += 1
        return [{"what": a["what"], "preserved": True} for a in anchors]

    def fake_detect(img, item, considered, **kw):
        calls["detect_kw"].append(kw)
        return {"anchors": [{"category": "other", "what": "얼룩", "where": "앞면"}],
                "text_level": text_level, "item_box": item_box}

    def fake_cosine(orig, result, **kw):
        calls["cosine"] += 1
        return 0.9

    def fake_compose(original, bg, box=None):
        calls["compose"] += 1
        return make_png(color=COMP_COLOR)

    monkeypatch.setattr(pipeline_mod.detector, "classify",
                        lambda img: {"item": "chair", "considered": []})
    monkeypatch.setattr(pipeline_mod.detector, "detect_full", fake_detect)
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
    """check_photo 는 가드와 동시에 시작하므로 가드가 막은 1번째 생성본에도 불릴 수
    있다 (결과는 버림, 시작 전이면 취소). 호출 순서가 스레드에 따라 바뀔 수 있어 next(iter) 대신 생성본마다
    다른 이미지를 만들고 이미지로 판정을 고른다: 1번(가드 차단)=버려질 invalid,
    2번(seed 재시도)=invalid → 재생성, 3번=valid."""
    monkeypatch.setattr(settings, "max_generate_attempts", 2, raising=False)
    calls = _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], [], []])
    gens = [make_png(color=(10 + 40 * i, 20, 30)) for i in range(3)]
    verdicts = {gens[0]: {"valid": False, "reason": "discarded"},
                gens[1]: {"valid": False, "reason": "cropped"},
                gens[2]: {"valid": True, "reason": ""}}

    def gen(original, preset, seed=None):
        calls["gen_seeds"].append(seed)
        return gens[len(calls["gen_seeds"]) - 1]
    monkeypatch.setattr(pipeline_mod, "_generate_ai", gen)
    photo_calls = []
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: photo_calls.append(img) or verdicts[img])

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    seeds = calls["gen_seeds"]
    assert len(seeds) == 3
    assert seeds[0] is None and isinstance(seeds[1], int) and seeds[2] is None
    assert out["gen_attempts"] == 2
    assert out["status"] == "pass"
    assert out["photo_check"] == {"valid": True, "reason": ""}
    # 통과한 2·3번은 반드시 검사. 차단된 1번은 시작 전이면 취소되고, 이미 시작했으면
    # 호출되지만 결과는 버려진다 — 어느 쪽이든 예산을 쓰지 않는다 (타이밍 의존이라 둘 다 허용).
    assert gens[1] in photo_calls and gens[2] in photo_calls
    assert len(photo_calls) in (2, 3) and set(photo_calls) <= set(gens)


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


def test_graph_item_pair_feeds_item_guard_not_run_output_guards(monkeypatch, make_png):
    """_item_pair 결과는 run_output_guards 가 아니라 item_guard 로 간다."""
    _patch_graph_deps(monkeypatch, make_png, [[_dino_ok(0.9)]])
    monkeypatch.setattr(pipeline_mod, "_item_pair", lambda s: (b"OI", b"RI"))
    seen = []
    monkeypatch.setattr(pipeline_mod.guards, "item_guard",
                        lambda pair: seen.append(pair) or _item_ok(0.91))

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert seen == [(b"OI", b"RI")]
    assert out["item_similarity"] == 0.91 and out["visual_similarity"] == 0.9


def test_graph_cutout_failure_gives_no_item_similarity(monkeypatch, make_png):
    """conftest 가 오리기를 막는다 → _item_pair 는 None → item_dino 생략, 판정 영향 없음."""
    _patch_graph_deps(monkeypatch, make_png, [[]])
    real_item_guard = pipeline_mod.guards.item_guard
    pairs = []
    monkeypatch.setattr(pipeline_mod.guards, "item_guard",
                        lambda pair: pairs.append(pair) or real_item_guard(pair))
    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})
    assert pairs == [None]
    assert out["status"] == "pass" and out["item_similarity"] is None
    assert out["guard_report"] == []


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

    def fake_verify(img, anchors, item, considered, **kw):
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

    def guards_first_fail_each_round(orig, result, a, b):
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

    def first_fails(orig, result, a, b):
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
    """detect_full 이 호출될 때마다 seq 항목을 차례로: Exception 이면 raise, 목록이면
    그 앵커 + text_level simple (예전 동작), dict 면 그대로 반환."""
    it = iter(seq)
    calls = []

    def fake(img, item, considered, **kw):
        calls.append({"item": item, **kw})
        v = next(it)
        if isinstance(v, Exception):
            raise v
        if isinstance(v, dict):
            return v
        return {"anchors": v, "text_level": "simple", "item_box": None}
    monkeypatch.setattr(pipeline_mod.detector, "detect_full", fake)
    return calls


_DETECT_FAILED = {"anchors": [], "detect_failed": True, "text_level": None}


def _detect_ok(anchors, text_level="simple", item_box=None):
    return {"anchors": anchors, "detect_failed": False, "text_level": text_level,
            "item_box": item_box}


def _no_vlm_verify(monkeypatch):
    def poison(*a, **k):
        raise AssertionError("verify VLM 호출 금지")
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate", poison)


def test_detect_both_attempts_fail_marks_detect_failed(monkeypatch, no_detect_sleep):
    calls = _detect_seq(monkeypatch, [ValueError("no defects"), RuntimeError("down")])
    out = pipeline_mod.detect({"original": b"x", "item": "chair", "considered": []})
    assert out == _DETECT_FAILED
    assert "item_box" not in out      # 실패는 박스를 모른다 — 덮어쓰지 않는다
    assert len(calls) == pipeline_mod.DETECT_ATTEMPTS == 2
    assert all(c["strict"] is True for c in calls)
    # 시도 사이 1회만 쉰다 (마지막 실패 뒤엔 안 쉼)
    assert no_detect_sleep == [pipeline_mod.DETECT_RETRY_DELAY_S]


def test_detect_first_fail_then_success(monkeypatch, no_detect_sleep):
    anchors = [{"category": "other", "what": "얼룩", "where": "앞면"}]
    calls = _detect_seq(monkeypatch, [ValueError("empty"), anchors])
    out = pipeline_mod.detect({"original": b"x"})
    assert out == _detect_ok(anchors)
    assert [c["item"] for c in calls] == ["object", "object"]
    assert no_detect_sleep == [pipeline_mod.DETECT_RETRY_DELAY_S]


def test_detect_first_success_no_retry(monkeypatch, no_detect_sleep):
    calls = _detect_seq(monkeypatch, [[]])
    out = pipeline_mod.detect({"original": b"x", "item": "cup"})
    assert out == _detect_ok([])   # 진짜 "하자 없음"
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
    assert pipeline_mod.verify(s) == {"checks": [], "gate_passed": None, "verify_failed": False}


def test_verify_composite_mode_excludes_text_targets(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate",
                        lambda img, t, i, c, **kw: seen.append(t) or
                        [{"what": x["what"], "preserved": True} for x in t])
    anchor = {"category": "other", "what": "얼룩", "where": "앞면"}
    out = pipeline_mod.verify({"anchors": [anchor], "item_texts": [{"text": "ABC"}],
                               "mode": "composite", "result_name": "r.jpg"})
    assert seen == [[anchor]] and out["gate_passed"] is True


def test_verify_composite_mode_texts_only_skips_vlm(monkeypatch):
    _no_vlm_verify(monkeypatch)
    out = pipeline_mod.verify({"anchors": [], "item_texts": [{"text": "ABC"}],
                               "mode": "composite", "result_name": "r.jpg"})
    assert out == {"checks": [], "gate_passed": None, "verify_failed": False}


# ── verify 호출 실패 ≠ "보존됨" (예전엔 gate None 으로 남아 통과됐다) ──
_ANCHOR = {"category": "other", "what": "a", "where": "b"}


class _Calls(list):
    def __init__(self):
        super().__init__()
        self.kwargs = []


def _verify_raises(monkeypatch, seq):
    """verify_and_locate 가 seq 항목을 차례로: Exception 이면 raise, 아니면 반환.
    호출 kwargs 는 calls.kwargs 에 남는다."""
    it = iter(seq)
    calls = _Calls()
    kwargs = calls.kwargs

    def fake(img, targets, item, considered, **kw):
        calls.append(img)
        kwargs.append(kw)
        v = next(it)
        if isinstance(v, Exception):
            raise v
        return v
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate", fake)
    return calls


def test_verify_vlm_exception_twice_generate_mode_fails_gate(monkeypatch, no_detect_sleep):
    calls = _verify_raises(monkeypatch, [RuntimeError("vlm down"), TimeoutError("t/o")])
    out = pipeline_mod.verify({"anchors": [_ANCHOR], "result_name": "r.jpg"})
    assert out == {"checks": [], "verify_failed": True, "gate_passed": False}
    assert len(calls) == pipeline_mod.VERIFY_ATTEMPTS == 2
    # 시도 사이 1회만 쉰다 (마지막 실패 뒤엔 안 쉼)
    assert no_detect_sleep == [pipeline_mod.DETECT_RETRY_DELAY_S]


@pytest.mark.parametrize("mode", ["generate", "composite_failed"])
def test_verify_vlm_exception_non_composite_modes_fail_gate(monkeypatch, mode):
    _verify_raises(monkeypatch, [RuntimeError("x"), RuntimeError("y")])
    out = pipeline_mod.verify({"anchors": [_ANCHOR], "result_name": "r.jpg", "mode": mode})
    assert out["verify_failed"] is True and out["gate_passed"] is False


def test_verify_vlm_exception_twice_composite_mode_is_unverified(monkeypatch):
    _verify_raises(monkeypatch, [RuntimeError("x"), RuntimeError("y")])
    out = pipeline_mod.verify({"anchors": [_ANCHOR], "result_name": "r.jpg",
                               "mode": "composite"})
    assert out == {"checks": [], "verify_failed": True, "gate_passed": None}


def test_verify_first_fail_then_success(monkeypatch, no_detect_sleep):
    checks = [{"what": "a", "preserved": True}]
    calls = _verify_raises(monkeypatch, [RuntimeError("429"), checks])
    out = pipeline_mod.verify({"anchors": [_ANCHOR], "result_name": "r.jpg"})
    assert out == {"checks": checks, "verify_failed": False, "gate_passed": True}
    assert len(calls) == 2
    assert no_detect_sleep == [pipeline_mod.DETECT_RETRY_DELAY_S]


def test_verify_first_success_no_retry_no_sleep(monkeypatch, no_detect_sleep):
    checks = [{"what": "a", "preserved": False}]
    calls = _verify_raises(monkeypatch, [checks])
    out = pipeline_mod.verify({"anchors": [_ANCHOR], "result_name": "r.jpg"})
    assert out == {"checks": checks, "verify_failed": False, "gate_passed": False}
    assert len(calls) == 1 and no_detect_sleep == []


def test_verify_retry_reuses_loaded_result(monkeypatch):
    """저장본은 한 번만 읽고 두 시도가 같은 바이트로 검사한다."""
    storage.save("result", "r.jpg", GEN_PNG)
    saved = storage.load("result", "r.jpg")
    loads = []
    real_load = storage.load
    monkeypatch.setattr(pipeline_mod.storage, "load",
                        lambda kind, name: loads.append((kind, name)) or real_load(kind, name))
    calls = _verify_raises(monkeypatch, [RuntimeError("x"), [{"what": "a", "preserved": True}]])
    pipeline_mod.verify({"anchors": [_ANCHOR], "result_name": "r.jpg"})
    assert loads == [("result", "r.jpg")]
    assert calls == [saved, saved]


def test_verify_success_short_answer_fails_gate_via_expected(monkeypatch):
    """성공이어도 답이 대상 수보다 적으면 all_preserved(expected=) 가 실패 처리."""
    _verify_raises(monkeypatch, [[{"what": "a", "preserved": True}]])
    out = pipeline_mod.verify({"anchors": [_ANCHOR, {**_ANCHOR, "what": "b"}],
                               "result_name": "r.jpg"})
    assert out["verify_failed"] is False and out["gate_passed"] is False


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
    # verify 호출 실패: 재생성 없이 바로 배경 교체 (재시도 전이든 후든)
    ({"gate_passed": False, "verify_failed": True}, "composite"),
    ({"gate_passed": False, "verify_failed": True, "gate_retried": True}, "composite"),
    ({"gate_passed": None, "verify_failed": True, "mode": "composite"}, "done"),
    ({"gate_passed": False, "verify_failed": True, "mode": "composite_failed"}, "done"),
    ({"gate_passed": False, "verify_failed": False}, "regen"),
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
    # 글자 수(text_heavy)는 plan 이 아니라 read_text 가 본다 — plan 땐 아직 안 읽었다
    ({"item_texts": _texts(12)}, 12, 0, None),
    ({"item_texts": _texts(40)}, 0, 0, None),
    ({"item_texts": _texts(1)}, 1, 0, None),
    ({"item_texts": None}, 12, 0, None),
    # text_level: dense 만 배경 교체 (min_texts 설정과 무관)
    ({"text_level": "dense"}, 12, 0, "text_dense"),
    ({"text_level": "dense"}, 0, 0, "text_dense"),
    ({"text_level": "simple"}, 12, 0, None),
    ({"text_level": "none"}, 12, 0, None),
    ({"text_level": None}, 12, 0, None),
    ({"anchors": _anchors(50)}, 12, 0, None),                  # 기본 끔
    ({"anchors": _anchors(2)}, 12, 3, None),
    ({"anchors": _anchors(3)}, 12, 3, "many_defects"),
    ({"anchors": None}, 12, 1, None),
    # 우선순위: detect_failed > text_dense > many_defects
    ({"detect_failed": True, "text_level": "dense", "anchors": _anchors(3)}, 12, 3, "detect_failed"),
    ({"text_level": "dense", "anchors": _anchors(3)}, 12, 3, "text_dense"),
    ({"text_level": "none", "anchors": _anchors(3)}, 12, 3, "many_defects"),
    ({"text_level": "simple", "item_texts": _texts(12), "anchors": _anchors(3)}, 12, 3,
     "many_defects"),
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
    assert pipeline_mod.plan({**s, "detect_failed": False,
                              "text_level": "dense"}) == {"result_name": "f_p.jpg"}


def test_plan_dense_sets_text_dense():
    out = pipeline_mod.plan({"file_id": "f", "preset_key": "p", "text_level": "dense"})
    assert out == {"result_name": "f_p.jpg", "composite_reason": "text_dense"}


@pytest.mark.parametrize("state,expected", [
    ({"composite_reason": "text_dense", "text_level": "dense"}, "composite"),
    ({"composite_reason": "detect_failed", "text_level": None}, "composite"),
    ({"composite_reason": None}, "generate"),       # conftest: text_lock 꺼짐
    ({"text_level": "simple"}, "generate"),         # 꺼져 있으면 simple 도 읽지 않는다
    ({}, "generate"),
])
def test_route_after_plan(state, expected):
    assert pipeline_mod._route_after_plan(state) == expected


@pytest.mark.parametrize("state,expected", [
    ({"text_level": "simple"}, "read_text"),
    ({"text_level": None}, "read_text"),       # 예전 state·detect 실패 = simple 로 본다
    ({}, "read_text"),
    ({"text_level": "none"}, "generate"),      # 글자 없음 — VLM 읽기 생략
    ({"text_level": "dense"}, "generate"),     # 사유 없이 dense 면 _needs_text 가 거른다
    ({"text_level": "simple", "composite_reason": "many_defects"}, "composite"),
    ({"text_level": "none", "composite_reason": "many_defects"}, "composite"),
    ({"text_level": "dense", "composite_reason": "text_dense"}, "composite"),
])
def test_route_after_plan_text_lock_on(monkeypatch, state, expected):
    monkeypatch.setattr(settings, "text_lock", True)
    assert pipeline_mod._route_after_plan(state) == expected


@pytest.mark.parametrize("lock,state,expected", [
    (True, {"text_level": "simple"}, True), (True, {}, True), (True, {"text_level": None}, True),
    (True, {"text_level": "none"}, False), (True, {"text_level": "dense"}, False),
    (True, {"text_level": ""}, True),          # 빈 문자열도 "모름" = simple
    (False, {"text_level": "simple"}, False), (False, {}, False),
])
def test_needs_text(monkeypatch, lock, state, expected):
    monkeypatch.setattr(settings, "text_lock", lock)
    assert pipeline_mod._needs_text(state) is expected


@pytest.mark.parametrize("state,expected", [
    ({"composite_reason": "text_heavy"}, "composite"),
    ({"composite_reason": None}, "generate"),
    ({}, "generate"),
])
def test_route_after_read_text(state, expected):
    assert pipeline_mod._route_after_read_text(state) == expected


@pytest.mark.parametrize("lock,state,expected", [
    (True, {"text_level": "simple"}, "read_text"),
    (True, {"text_level": None}, "read_text"),
    (True, {"text_level": "none"}, "use_provided"),
    (True, {"text_level": "dense"}, "use_provided"),
    (True, {"text_level": "dense", "composite_reason": "text_dense"}, "use_provided"),
    (False, {"text_level": "simple"}, "use_provided"),
])
def test_route_after_plan_dev(monkeypatch, lock, state, expected):
    monkeypatch.setattr(settings, "text_lock", lock)
    assert pipeline_mod._route_after_plan_dev(state) == expected


def test_graph_text_heavy_goes_to_composite_without_generate(monkeypatch, make_png):
    """detect 는 simple 이라 했지만 read_text 가 읽어 보니 12줄 → read_text 가 사유를 달고
    바로 배경 교체 (plan 은 읽기 전이라 사유를 달지 않는다)."""
    calls = _patch_graph_deps(monkeypatch, make_png, [])
    monkeypatch.setattr(settings, "text_lock", True)
    reads = _text_reader(monkeypatch, _texts(12), [])
    plans = []
    real_plan = pipeline_mod.plan
    monkeypatch.setattr(pipeline_mod, "plan", lambda s: plans.append(r := real_plan(s)) or r)
    monkeypatch.setattr(pipeline_mod, "GRAPH", pipeline_mod.build())
    seen = []
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate",
                        lambda img, t, i, c, **kw: seen.append(t) or
                        [{"what": x["what"], "preserved": True} for x in t])

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert calls["gen_seeds"] == []
    assert out["mode"] == "composite" and out["composite_reason"] == "text_heavy"
    assert plans == [{"result_name": "fid-g_studio_white.jpg"}]   # plan 은 사유 없음
    assert len(reads) == 1                      # 원본만 읽음 (OCR 가드 안 탐)
    assert calls["detect_kw"] == [{"strict": True}]
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
    assert "item_similarity" in out and out["item_similarity"] is None
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

    def fake_verify(img, targets, item, considered, **kw):
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
                        lambda img, t, i, c, **kw: calls.append(t) or
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

    def fake_verify(img, targets, item, considered, **kw):
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

    def spy(orig, result, before, after):
        seen.update(before=before, after=after)
        return [_dino_ok(0.81)]
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", spy)

    out = pipeline_mod._run_guards(
        {"original": b"O", "result": b"R", "item_texts": [{"text": "AB"}], "anchors": []})

    assert out == ("pass", [], 0.81)       # 3-tuple — 누끼 유사도는 여기서 안 다룬다
    assert seen == {"before": ["AB"], "after": ["AB"]}


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
                                 "visual_similarity": 0.99, "item_similarity": 0.95})
    assert out["visual_similarity"] is None and out["result"] == b"NEW"
    assert "item_similarity" in out and out["item_similarity"] is None


# ══ judge: 캐시 없음 + defer ═════════════════════════
def test_clear_quality_swallows_and_targets_quality_kind(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline_mod.storage, "delete", lambda k, n: seen.append((k, n)))
    pipeline_mod._clear_quality("a.json")
    assert seen == [("quality", "a.json")]

    def boom(k, n):
        raise RuntimeError("x")
    monkeypatch.setattr(pipeline_mod.storage, "delete", boom)
    pipeline_mod._clear_quality("a.json")        # 예외 없음


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
    assert storage.load("result", "fid-g_studio_white.jpg") is not None
    assert storage.load("quality", "fid-g_studio_white.json") is None


def test_graph_inline_judge_runs_once_on_saved_result(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    out = pipeline_mod.run_transform("fid-g", "studio_white")
    assert calls["judge"] == 1 and out["judge_pending"] is False
    assert json.loads(storage.load("quality", "fid-g_studio_white.json"))["analysis"] == "ok"


# ══ judge_and_save (채점 구현 단일 경로) ═════════════
REPORT = {"analysis": "bg", "fidelity": 4, "realism": 3, "trust": 5}


@pytest.fixture()
def jl(monkeypatch):
    """judge_and_save 공통 준비: 원본/결과 저장 + flush/score 기록."""
    storage.save("original", "fjl.png", ORIG_PNG)
    storage.save("result", "fjl_p.jpg", GEN_PNG)
    rec = {"flush": 0, "scores": [], "judge_args": []}
    monkeypatch.setattr(pipeline_mod, "flush", lambda: rec.__setitem__("flush", rec["flush"] + 1))
    monkeypatch.setattr(pipeline_mod, "score",
                        lambda name, v, **kw: rec["scores"].append((name, v)))
    monkeypatch.setattr(pipeline_mod.judge, "judge",
                        lambda o, r: rec["judge_args"].append((o, r)) or dict(REPORT))
    return rec


def test_judge_and_save_saves_report_and_scores(jl):
    pipeline_mod.judge_and_save("fjl", "p")
    [(o, r)] = jl["judge_args"]
    assert _close(_mean_color(o), ORIG_COLOR) and _close(_mean_color(r), GEN_COLOR)
    assert json.loads(storage.load("quality", "fjl_p.json")) == REPORT
    from app.prompts.rubric import AXES
    assert jl["scores"] == [(a, REPORT[a]) for a in AXES]
    assert jl["flush"] == 1


@pytest.mark.parametrize("missing", ["original", "result"])
def test_judge_and_save_missing_input_returns_and_flushes(jl, missing, tmp_storage):
    if missing == "original":
        (tmp_storage / "original" / "fjl.png").unlink()
    else:
        (tmp_storage / "result" / "fjl_p.jpg").unlink()
    pipeline_mod.judge_and_save("fjl", "p")
    assert jl["judge_args"] == [] and jl["flush"] == 1
    assert storage.load("quality", "fjl_p.json") is None


@pytest.mark.parametrize("empty", [None, {}])
def test_judge_and_save_empty_report_saves_nothing(jl, monkeypatch, empty):
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: empty)
    pipeline_mod.judge_and_save("fjl", "p")
    assert storage.load("quality", "fjl_p.json") is None
    assert jl["scores"] == [] and jl["flush"] == 1


def test_judge_and_save_discards_when_result_changes_during_judge(jl, monkeypatch):
    def slow_judge(o, r):
        storage.save("result", "fjl_p.jpg", COMP_PNG)    # 채점 중 재변환
        return dict(REPORT)
    monkeypatch.setattr(pipeline_mod.judge, "judge", slow_judge)
    pipeline_mod.judge_and_save("fjl", "p")
    assert storage.load("quality", "fjl_p.json") is None
    assert jl["scores"] == [] and jl["flush"] == 1


def test_judge_and_save_result_changes_between_check_and_save_deletes_quality(jl, monkeypatch):
    real_save = storage.save

    def racing_save(kind, name, data):
        real_save(kind, name, data)
        if kind == "quality":
            real_save("result", "fjl_p.jpg", COMP_PNG)   # 저장 직후 재변환이 끼어듦
    monkeypatch.setattr(pipeline_mod.storage, "save", racing_save)

    pipeline_mod.judge_and_save("fjl", "p")

    assert storage.load("quality", "fjl_p.json") is None
    assert jl["scores"] == [] and jl["flush"] == 1


def test_judge_and_save_result_deleted_during_judge_discards(jl, monkeypatch, tmp_storage):
    def judge_then_delete(o, r):
        (tmp_storage / "result" / "fjl_p.jpg").unlink()
        return dict(REPORT)
    monkeypatch.setattr(pipeline_mod.judge, "judge", judge_then_delete)
    pipeline_mod.judge_and_save("fjl", "p")
    assert storage.load("quality", "fjl_p.json") is None


@pytest.mark.parametrize("where", ["judge", "load", "save", "score"])
def test_judge_and_save_swallows_exceptions_and_flushes(jl, monkeypatch, where):
    def boom(*a, **k):
        raise RuntimeError(where)
    target = {"judge": (pipeline_mod.judge, "judge"),
              "load": (pipeline_mod.storage, "load_original"),
              "save": (pipeline_mod.storage, "save"),
              "score": (pipeline_mod, "score")}[where]
    monkeypatch.setattr(*target, boom)
    pipeline_mod.judge_and_save("fjl", "p")        # 예외 없음
    assert jl["flush"] == 1


def test_judge_and_save_score_failure_keeps_saved_report(jl, monkeypatch):
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: {"analysis": "no axes"})
    pipeline_mod.judge_and_save("fjl", "p")
    assert storage.load("quality", "fjl_p.json") is not None and jl["flush"] == 1


def test_judge_and_save_attaches_to_given_trace_and_parent(monkeypatch):
    fake_lf = _enable_fake_langfuse(monkeypatch)
    storage.save("original", "fjl.png", ORIG_PNG)
    storage.save("result", "fjl_p.jpg", GEN_PNG)
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: dict(REPORT))

    pipeline_mod.judge_and_save("fjl", "p", trace_id="trace-9", parent_span_id="span-7")

    call = fake_lf.calls[0]
    assert call["name"] == "judge_and_save" and call["as_type"] == "span"
    assert call["trace_context"] == {"trace_id": "trace-9", "parent_span_id": "span-7"}
    assert call["input"] == {"file_id": "fjl", "preset_key": "p"}
    assert fake_lf.flushed is True


def test_judge_and_save_trace_without_parent(monkeypatch):
    fake_lf = _enable_fake_langfuse(monkeypatch)
    storage.save("original", "fjl.png", ORIG_PNG)
    storage.save("result", "fjl_p.jpg", GEN_PNG)
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: dict(REPORT))
    pipeline_mod.judge_and_save("fjl", "p", trace_id="trace-9")
    assert fake_lf.calls[0]["trace_context"] == {"trace_id": "trace-9"}


def test_judge_and_save_no_trace_id_no_trace_context(monkeypatch):
    fake_lf = _enable_fake_langfuse(monkeypatch)
    storage.save("original", "fjl.png", ORIG_PNG)
    storage.save("result", "fjl_p.jpg", GEN_PNG)
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: dict(REPORT))
    pipeline_mod.judge_and_save("fjl", "p", trace_id=None, parent_span_id="span-7")
    assert "trace_context" not in fake_lf.calls[0]


def test_judge_and_save_trace_args_are_keyword_only():
    with pytest.raises(TypeError):
        pipeline_mod.judge_and_save("fjl", "p", "trace-9")
    with pytest.raises(TypeError):
        pipeline_mod.judge_and_save("fjl", "p", "trace-9", "span-7")


def test_judge_and_save_rejudges_even_if_quality_file_exists(jl):
    storage.save("quality", "fjl_p.json", b'{"stale": true}')
    pipeline_mod.judge_and_save("fjl", "p")
    assert len(jl["judge_args"]) == 1
    assert json.loads(storage.load("quality", "fjl_p.json")) == REPORT


def test_judge_and_save_does_not_need_delete_permission(jl, monkeypatch):
    """정상 경로에서는 delete 를 부르지 않는다 (S3 DeleteObject 권한 없어도 저장됨)."""
    def boom(kind, name):
        raise PermissionError("s3:DeleteObject denied")
    monkeypatch.setattr(pipeline_mod.storage, "delete", boom)
    pipeline_mod.judge_and_save("fjl", "p")
    assert json.loads(storage.load("quality", "fjl_p.json")) == REPORT


def test_judge_and_save_race_clear_failure_is_swallowed(jl, monkeypatch):
    """저장 직후 결과가 바뀌어 지우려는데 delete 가 실패해도 예외 없이 끝나고 점수는 안 붙는다."""
    real_save = storage.save

    def racing_save(kind, name, data):
        real_save(kind, name, data)
        if kind == "quality":
            real_save("result", "fjl_p.jpg", COMP_PNG)
    monkeypatch.setattr(pipeline_mod.storage, "save", racing_save)

    def boom(kind, name):
        raise PermissionError("denied")
    monkeypatch.setattr(pipeline_mod.storage, "delete", boom)
    pipeline_mod.judge_and_save("fjl", "p")
    assert jl["scores"] == [] and jl["flush"] == 1


def test_judge_and_save_partial_scores_keep_report(jl, monkeypatch):
    """축 하나만 빠져도 앞 축 점수는 이미 붙고, 보고서는 남는다."""
    from app.prompts.rubric import AXES
    partial = {"analysis": "x", AXES[0]: 3}
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: dict(partial))
    pipeline_mod.judge_and_save("fjl", "p")
    assert json.loads(storage.load("quality", "fjl_p.json")) == partial
    assert jl["scores"] == [(AXES[0], 3)] and jl["flush"] == 1


# ── run_transform ↔ judge_and_save (옛 run_judge 노드 테스트에서 옮김) ──
def _seed_pair(fid="frt", preset="p"):
    storage.save("original", f"{fid}.png", ORIG_PNG)
    storage.save("result", f"{fid}_{preset}.jpg", GEN_PNG)


def test_run_transform_clears_stale_quality_before_graph(monkeypatch):
    storage.save("quality", "frt_p.json", b'{"stale": true}')
    seen = {}

    class CheckGraph(FakeGraph):
        def invoke(self, state, config=None):
            seen["at_invoke"] = storage.load("quality", "frt_p.json")
            return super().invoke(state, config)
    monkeypatch.setattr(pipeline_mod, "GRAPH", CheckGraph(_graph_out()), raising=False)
    pipeline_mod.run_transform("frt", "p", defer_judge=True)
    assert seen["at_invoke"] is None
    assert storage.load("quality", "frt_p.json") is None


@pytest.mark.parametrize("defer", [True, False])
def test_run_transform_blocked_clears_stale_and_never_judges(monkeypatch, defer):
    _seed_pair()
    storage.save("quality", "frt_p.json", b'{"stale": true}')

    def poison(*a, **k):
        raise AssertionError("blocked 에서 judge 금지")
    monkeypatch.setattr(pipeline_mod.judge, "judge", poison)
    monkeypatch.setattr(pipeline_mod, "GRAPH",
                        FakeGraph(_graph_out(status="blocked")), raising=False)
    out = pipeline_mod.run_transform("frt", "p", defer_judge=defer)
    assert out["judge_pending"] is False
    assert storage.load("quality", "frt_p.json") is None


def test_run_transform_stale_report_written_during_graph_is_cleared(monkeypatch):
    """이전 요청의 백그라운드 채점이 그래프 도중(새 결과 저장 전)에 옛 점수를 저장해도
    defer 응답 뒤엔 남아 있으면 안 된다 — 그래프 직후 한 번 더 지운다."""
    class RacingGraph(FakeGraph):
        def invoke(self, state, config=None):
            storage.save("quality", "frt_p.json", b'{"stale": "from old bg judge"}')
            return super().invoke(state, config)
    monkeypatch.setattr(pipeline_mod, "GRAPH", RacingGraph(_graph_out()), raising=False)
    out = pipeline_mod.run_transform("frt", "p", defer_judge=True)
    assert out["judge_pending"] is True
    assert storage.load("quality", "frt_p.json") is None


def test_run_transform_blocked_stale_report_written_during_graph_is_cleared(monkeypatch):
    class RacingGraph(FakeGraph):
        def invoke(self, state, config=None):
            storage.save("quality", "frt_p.json", b'{"stale": true}')
            return super().invoke(state, config)
    monkeypatch.setattr(pipeline_mod, "GRAPH",
                        RacingGraph(_graph_out(status="blocked")), raising=False)
    pipeline_mod.run_transform("frt", "p")
    assert storage.load("quality", "frt_p.json") is None


def test_run_transform_with_result_stale_report_written_during_graph_is_cleared(
        monkeypatch, make_png):
    judged = _patch_nodes(monkeypatch, [], True, [])

    def racing_finalize(s):
        storage.save("quality", "fw_p.json", b'{"stale": true}')
        return {"bubbles": []}
    monkeypatch.setattr(pipeline_mod, "finalize", racing_finalize)
    pipeline_mod.run_transform_with_result("fw", "p", make_png())
    assert storage.load("quality", "fw_p.json") is None    # judge_and_save 는 no-op 스파이
    assert judged == [("fw", "p", {})]


def test_run_transform_delete_failure_is_swallowed_and_still_judges(monkeypatch):
    _seed_pair()

    def boom(kind, name):
        raise PermissionError("s3:DeleteObject denied")
    monkeypatch.setattr(pipeline_mod.storage, "delete", boom)
    monkeypatch.setattr(pipeline_mod.judge, "judge",
                        lambda o, r: {"fidelity": 2, "realism": 2, "trust": 2})
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out()), raising=False)
    out = pipeline_mod.run_transform("frt", "p")
    assert out["judge_pending"] is False
    assert json.loads(storage.load("quality", "frt_p.json"))["fidelity"] == 2


def test_run_transform_delete_failure_is_swallowed_when_deferred(monkeypatch):
    def boom(kind, name):
        raise PermissionError("denied")
    monkeypatch.setattr(pipeline_mod.storage, "delete", boom)
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out()), raising=False)
    assert pipeline_mod.run_transform("frt", "p", defer_judge=True)["judge_pending"] is True


def test_run_transform_judge_exception_leaves_no_stale_file(monkeypatch):
    _seed_pair()
    storage.save("quality", "frt_p.json", b'{"stale": true}')

    def boom(*a):
        raise RuntimeError("judge down")
    monkeypatch.setattr(pipeline_mod.judge, "judge", boom)
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out()), raising=False)
    out = pipeline_mod.run_transform("frt", "p")          # 예외 안 새어 나옴
    assert out["result_name"] == "abc_preset.jpg"
    assert storage.load("quality", "frt_p.json") is None


@pytest.mark.parametrize("empty", [None, {}])
def test_run_transform_empty_report_leaves_no_file(monkeypatch, empty):
    _seed_pair()
    storage.save("quality", "frt_p.json", b'{"stale": true}')
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: empty)
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out()), raising=False)
    pipeline_mod.run_transform("frt", "p")
    assert storage.load("quality", "frt_p.json") is None


def test_run_transform_score_failure_still_saves_report(monkeypatch):
    _seed_pair()
    monkeypatch.setattr(pipeline_mod.judge, "judge", lambda o, r: {"analysis": "partial"})
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out()), raising=False)
    pipeline_mod.run_transform("frt", "p")
    assert json.loads(storage.load("quality", "frt_p.json")) == {"analysis": "partial"}


def test_run_transform_inline_judge_missing_result_is_noop(monkeypatch):
    """그래프가 결과를 안 남겼으면(가짜 그래프) judge_and_save 는 조용히 빠진다."""
    storage.save("original", "frt.png", ORIG_PNG)

    def poison(*a):
        raise AssertionError("결과 없으면 judge 금지")
    monkeypatch.setattr(pipeline_mod.judge, "judge", poison)
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out()), raising=False)
    pipeline_mod.run_transform("frt", "p")
    assert storage.load("quality", "frt_p.json") is None


# ══ 그래프 연결 ═════════════════════════════════════
def test_detect_then_read_text_then_single_generate(monkeypatch, make_png):
    """detect 가 먼저(text_level 판단) → simple 이면 plan 뒤 read_text → 생성 1회."""
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    monkeypatch.setattr(settings, "text_lock", True)
    order = []
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text",
                        lambda img, item, **kw: order.append("read_result" if kw.get("strict") else "read") or {
                            "item_box": None,
                            "texts": [{"text": "SALE", "x1": 0, "y1": 0, "x2": 100, "y2": 100}]})
    anchors = [{"category": "scratch", "what": "긁힘", "where": "뒷면"}]
    monkeypatch.setattr(pipeline_mod.detector, "detect_full",
                        lambda img, item, considered, **kw: order.append("detect") or {
                            "anchors": anchors, "text_level": "simple", "item_box": None})
    prompts = []

    def gen(original, preset, seed=None):
        order.append("gen")
        prompts.append(preset["prompt"])
        return make_png()
    monkeypatch.setattr(pipeline_mod, "_generate_ai", gen)

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    # 순서 고정: detect → read(원본) → 생성 1회 → OCR 가드용 결과 읽기
    assert order == ["detect", "read", "gen", "read_result"]
    assert '"SALE" (top-left)' in prompts[0]
    assert calls["ocr"] == [(["SALE"], ["SALE"])]
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["anchors"] == anchors
    assert ins["item_texts"][0]["text"] == "SALE"
    assert out["status"] == "pass" and out["gate_passed"] is True


def test_graph_edges_detect_then_plan_then_optional_read_text():
    g = pipeline_mod.GRAPH.get_graph()
    edges = {(e.source, e.target) for e in g.edges}
    outs = {}
    for s_, t in edges:
        outs.setdefault(s_, set()).add(t)
    # 병렬(read_text ∥ detect)이 아니라 순차: classify → detect → plan
    assert outs["classify"] == {"detect"}
    assert outs["detect"] == {"plan"}
    assert ("classify", "read_text") not in edges and ("read_text", "plan") not in edges
    assert ("read_text", "detect") not in edges and ("detect", "generate") not in edges
    assert outs["plan"] == {"composite", "read_text", "generate"}
    assert outs["read_text"] == {"composite", "generate"}
    assert ("validate_result", "composite") in edges
    assert outs["composite"] == {"generate", "read_text", "score_similarity", "save_inspect"}


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


# ══ validate_result: check_photo 를 가드와 동시에 (백그라운드) ══
class _FakeFuture:
    """_in_background 가 돌려줄 가짜 Future — cancel/result 호출을 기록."""
    def __init__(self, value=None, exc=None):
        self.value, self.exc = value, exc
        self.cancelled = 0
        self.result_timeouts = []

    def cancel(self):
        self.cancelled += 1
        return True

    def result(self, timeout=None):
        self.result_timeouts.append(timeout)
        if self.exc is not None:
            raise self.exc
        return self.value


def _spy_background(monkeypatch, future, item_future=None):
    """check_photo 제출엔 future, _item_signals 제출엔 item_future(기본: (None, None) 을
    돌려주는 가짜)를 돌려준다. 제출 기록 (fn, args, kwargs) 반환."""
    item_future = item_future or _FakeFuture((None, None))
    submitted = []

    def fake(fn, *a, **kw):
        submitted.append((fn, a, kw))
        return item_future if fn is pipeline_mod._item_signals else future
    monkeypatch.setattr(pipeline_mod, "_in_background", fake)
    return submitted


def test_validate_result_block_still_calls_check_photo_but_ignores_it(monkeypatch):
    """가드가 막아도 check_photo 는 (동시에 시작했으니) 불린다 — 결과는 버린다."""
    photo = []
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: photo.append(img) or {"valid": False, "reason": "cropped"})
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_hard_fail()])

    out = pipeline_mod.validate_result(_validate_state())

    assert out["guard_retry"] is True
    assert "photo_check" not in out and "status" not in out
    # 취소는 시작 전일 때만 된다 — 이미 시작했다면 끝까지 돈다. 여기선 시작 여부가
    # 스레드 타이밍에 달려 있어 "호출됐다면 결과 이미지로" 만 확인한다.
    assert photo in ([], [GEN_PNG])


def test_validate_result_block_submits_check_photo_then_cancels(monkeypatch):
    fut = _FakeFuture({"valid": False, "reason": "x"})
    item = _FakeFuture((None, None))
    submitted = _spy_background(monkeypatch, fut, item)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_hard_fail()])
    state = _validate_state(guard_seed=1)

    out = pipeline_mod.validate_result(state)

    # 막힌 시도엔 누끼를 시작하지 않는다 — check_photo 하나만
    assert submitted == [(pipeline_mod.detector.check_photo, (GEN_PNG,), {})]
    assert fut.cancelled == 1 and fut.result_timeouts == []
    assert item.result_timeouts == []
    assert out["guard_failed"] is True and "photo_check" not in out


def test_validate_result_block_check_photo_really_runs_in_graph_when_started(monkeypatch):
    """실제 풀: check_photo 가 가드보다 먼저 시작되도록 가드를 잠깐 붙잡으면
    (취소 불가 상태) 차단된 시도에서도 확실히 호출된다."""
    import threading
    started = threading.Event()
    photo = []

    def check(img):
        photo.append(img)
        started.set()
        return {"valid": False, "reason": "cropped"}

    def guards(*a):
        assert started.wait(5)
        return [_hard_fail()]
    monkeypatch.setattr(pipeline_mod.detector, "check_photo", check)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", guards)

    out = pipeline_mod.validate_result(_validate_state())

    assert photo == [GEN_PNG]
    assert "photo_check" not in out and out["guard_retry"] is True


def test_validate_result_guard_exception_cancels_photo_and_reraises(monkeypatch):
    fut = _FakeFuture({"valid": True, "reason": ""})
    submitted = _spy_background(monkeypatch, fut)

    def boom(*a):
        raise RuntimeError("guard crashed")
    monkeypatch.setattr(pipeline_mod, "_run_guards", boom)

    with pytest.raises(RuntimeError, match="guard crashed"):
        pipeline_mod.validate_result(_validate_state())
    assert fut.cancelled == 1
    assert [fn for fn, _, _ in submitted] == [pipeline_mod.detector.check_photo]   # 누끼 없음
    assert storage.load("result", "v_p.jpg") is None


def test_validate_result_run_output_guards_exception_cancels_photo_no_item(monkeypatch):
    """_run_guards 안쪽(실제 코드)에서 가드가 터지면 photo 취소, 누끼는 시작도 안 함."""
    fut = _FakeFuture({"valid": True, "reason": ""})
    submitted = _spy_background(monkeypatch, fut)

    def boom(*a, **k):
        raise RuntimeError("embedder down")
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", boom)

    with pytest.raises(RuntimeError, match="embedder down"):
        pipeline_mod.validate_result(_validate_state())
    assert fut.cancelled == 1
    assert [fn for fn, _, _ in submitted] == [pipeline_mod.detector.check_photo]


def test_validate_result_guard_keyboard_interrupt_also_cancels(monkeypatch):
    """BaseException 도 취소 후 그대로 올린다."""
    fut = _FakeFuture()
    submitted = _spy_background(monkeypatch, fut)

    def boom(*a):
        raise KeyboardInterrupt
    monkeypatch.setattr(pipeline_mod, "_run_guards", boom)

    with pytest.raises(KeyboardInterrupt):
        pipeline_mod.validate_result(_validate_state())
    assert fut.cancelled == 1 and len(submitted) == 1


def test_validate_result_pass_waits_with_timeout_and_uses_photo(monkeypatch):
    fut = _FakeFuture({"valid": False, "reason": "cropped"})
    item = _FakeFuture((None, None))
    _spy_background(monkeypatch, fut, item)
    monkeypatch.setattr(settings, "vlm_timeout_s", 7)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])

    out = pipeline_mod.validate_result(_validate_state())

    assert fut.result_timeouts == [17]
    [t] = item.result_timeouts        # 남은 시간 (마감은 제출 시점부터)
    assert 0 < t <= pipeline_mod.ITEM_WAIT_S
    assert fut.cancelled == 0 and item.cancelled == 0
    assert out["photo_check"] == {"valid": False, "reason": "cropped"}
    assert out["status"] == "pass"


def test_validate_result_saves_before_waiting_for_photo(monkeypatch):
    """가드 통과 → 저장이 photo.result() 보다 먼저."""
    order = []

    class F(_FakeFuture):
        def result(self, timeout=None):
            order.append(("wait", storage.load("result", "v_p.jpg") is not None))
            return {"valid": True, "reason": ""}
    _spy_background(monkeypatch, F())   # 누끼는 기본 가짜(None) — 기록 안 함
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])

    pipeline_mod.validate_result(_validate_state())

    assert order == [("wait", True)]


@pytest.mark.parametrize("exc", [RuntimeError("vlm down"), TimeoutError()])
def test_validate_result_photo_failure_or_timeout_is_valid(monkeypatch, exc):
    _spy_background(monkeypatch, _FakeFuture(exc=exc))
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])

    out = pipeline_mod.validate_result(_validate_state())

    assert out["photo_check"] == {"valid": True, "reason": ""}
    assert out["status"] == "pass"


def test_validate_result_check_photo_exception_in_real_pool_gives_valid(monkeypatch):
    """check_photo 는 스스로 예외를 삼키지만, 바꿔 끼운 함수가 던져도 Future 에서
    나온 예외를 validate_result 가 삼켜 valid=True."""
    def boom(img):
        raise RuntimeError("vlm down")
    monkeypatch.setattr(pipeline_mod.detector, "check_photo", boom)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])

    out = pipeline_mod.validate_result(_validate_state())

    assert out["photo_check"] == {"valid": True, "reason": ""}


def test_validate_result_real_check_photo_vlm_failure_gives_valid(monkeypatch):
    """실제 detector.check_photo + 실패하는 VLM 클라이언트 → 개방형 폴백."""
    def no_client():
        raise ConnectionError("no network")
    monkeypatch.setattr(pipeline_mod.detector, "get_client", no_client)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])

    out = pipeline_mod.validate_result(_validate_state())

    assert out["photo_check"] == {"valid": True, "reason": ""}


def test_validate_result_photo_real_timeout_gives_valid(monkeypatch):
    """check_photo 가 상한(vlm_timeout_s + 10)보다 오래 걸리면 기다리지 않고 valid=True."""
    import threading
    release = threading.Event()

    def slow(img):
        release.wait(5)
        return {"valid": False, "reason": "late"}
    monkeypatch.setattr(pipeline_mod.detector, "check_photo", slow)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])
    # 설정 검증(ge=1)을 우회해 상한을 0.05초로 — 실제 대기 없이 타임아웃 경로 확인
    monkeypatch.setattr(settings, "vlm_timeout_s", -9.95)
    try:
        out = pipeline_mod.validate_result(_validate_state())
    finally:
        release.set()

    assert out["photo_check"] == {"valid": True, "reason": ""}


def test_validate_result_check_photo_bound_at_submit_time(monkeypatch):
    """제출 시점의 detector.check_photo 가 쓰인다 — 가드 도중 바꿔 끼워도 영향 없음."""
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": False, "reason": "first"})

    def guards(*a):
        monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                            lambda img: {"valid": True, "reason": "second"})
        return []
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", guards)

    out = pipeline_mod.validate_result(_validate_state())

    assert out["photo_check"] == {"valid": False, "reason": "first"}


def test_validate_result_propagates_contextvars_to_check_photo(monkeypatch):
    """_in_background 가 현재 컨텍스트를 복사해 넘긴다 — 트레이스가 끊기지 않게."""
    import contextvars
    import threading
    var = contextvars.ContextVar("trace_ctx_test", default="unset")
    seen = {}

    def check(img):
        seen["value"] = var.get()
        seen["thread"] = threading.current_thread().name
        return {"valid": True, "reason": ""}
    monkeypatch.setattr(pipeline_mod.detector, "check_photo", check)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])

    token = var.set("trace-abc")
    try:
        pipeline_mod.validate_result(_validate_state())
    finally:
        var.reset(token)

    assert seen["value"] == "trace-abc"
    assert seen["thread"] != threading.current_thread().name
    assert seen["thread"].startswith("pipeline")


def test_in_background_context_is_a_copy_not_shared():
    """백그라운드에서 바꾼 contextvar 는 호출한 쪽에 새지 않는다."""
    import contextvars
    var = contextvars.ContextVar("copy_test", default="outer")

    def mutate():
        before = var.get()
        var.set("inner")
        return before, var.get()

    assert pipeline_mod._in_background(mutate).result(timeout=5) == ("outer", "inner")
    assert var.get() == "outer"


def test_in_background_forwards_args_and_exceptions():
    assert pipeline_mod._in_background(lambda a, b: a + b, 2, 3).result(timeout=5) == 5

    def boom():
        raise ValueError("x")
    with pytest.raises(ValueError):
        pipeline_mod._in_background(boom).result(timeout=5)


# ══ retryable: 다시 해도 똑같을 실패는 재시도하지 않는다 ══
def _client_error(code):
    from google.genai import errors
    return errors.ClientError(code, {"error": {"code": code, "message": "m", "status": "S"}})


def test_verify_passes_strict_true(monkeypatch):
    calls = _verify_raises(monkeypatch, [[{"what": "a", "preserved": True}]])
    pipeline_mod.verify({"anchors": [_ANCHOR], "result_name": "r.jpg"})
    assert calls.kwargs == [{"strict": True}]


@pytest.mark.parametrize("exc", [_client_error(400), TypeError("sig"), KeyError("k"),
                                 AttributeError("a")])
def test_verify_non_retryable_error_single_attempt(monkeypatch, no_detect_sleep, exc):
    calls = _verify_raises(monkeypatch, [exc, [{"what": "a", "preserved": True}]])
    out = pipeline_mod.verify({"anchors": [_ANCHOR], "result_name": "r.jpg"})
    assert out == {"checks": [], "verify_failed": True, "gate_passed": False}
    assert len(calls) == 1 and no_detect_sleep == []


@pytest.mark.parametrize("exc", [_client_error(429), ValueError("no checks"), TimeoutError()])
def test_verify_retryable_error_retries(monkeypatch, no_detect_sleep, exc):
    checks = [{"what": "a", "preserved": True}]
    calls = _verify_raises(monkeypatch, [exc, checks])
    out = pipeline_mod.verify({"anchors": [_ANCHOR], "result_name": "r.jpg"})
    assert out == {"checks": checks, "verify_failed": False, "gate_passed": True}
    assert len(calls) == 2 and no_detect_sleep == [pipeline_mod.DETECT_RETRY_DELAY_S]


def test_verify_non_retryable_uses_pipeline_retryable_name(monkeypatch, no_detect_sleep):
    """pipeline 은 retryable 을 이름으로 가져온다 — 그 이름을 바꾸면 판정이 바뀐다."""
    monkeypatch.setattr(pipeline_mod, "retryable", lambda e: False)
    calls = _verify_raises(monkeypatch, [RuntimeError("x"), RuntimeError("y")])
    out = pipeline_mod.verify({"anchors": [_ANCHOR], "result_name": "r.jpg",
                               "mode": "composite"})
    assert out == {"checks": [], "verify_failed": True, "gate_passed": None}
    assert len(calls) == 1


def test_verify_strict_bad_response_via_real_verify_and_locate(monkeypatch, no_detect_sleep):
    """실제 verify_and_locate(strict=True) + _call 이 {} (JSON 깨짐) → ValueError →
    재시도 1회 → 그래도 {} 면 verify_failed (예전처럼 '전부 사라짐'이 아니다)."""
    seen = []
    monkeypatch.setattr(pipeline_mod.detector, "_call",
                        lambda img, prompt, name="": seen.append(name) or {})
    out = pipeline_mod.verify({"anchors": [_ANCHOR], "result_name": "r.jpg"})
    assert out == {"checks": [], "verify_failed": True, "gate_passed": False}
    assert seen == ["verify", "verify"]


@pytest.mark.parametrize("exc", [_client_error(400), TypeError("sig")])
def test_detect_non_retryable_error_single_attempt(monkeypatch, no_detect_sleep, exc):
    calls = _detect_seq(monkeypatch, [exc, []])
    out = pipeline_mod.detect({"original": b"x"})
    assert out == _DETECT_FAILED
    assert len(calls) == 1 and no_detect_sleep == []


def test_detect_429_is_retried(monkeypatch, no_detect_sleep):
    calls = _detect_seq(monkeypatch, [_client_error(429), []])
    assert pipeline_mod.detect({"original": b"x"}) == _detect_ok([])
    assert len(calls) == 2 and no_detect_sleep == [pipeline_mod.DETECT_RETRY_DELAY_S]


# ══ 전체 그래프: verify 호출 실패 → 재생성 없이 배경 교체 ══
def test_graph_verify_always_fails_goes_composite_without_regen(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    vcalls = _verify_raises(monkeypatch, [RuntimeError("vlm down")] * 4)

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert calls["gen_seeds"] == [None]                 # FLUX 1회 — 재생성 없음
    assert calls["compose"] == 1
    assert out["mode"] == "composite"
    assert out["composite_reason"] == "verify_failed"
    assert out["verify_failed"] is True                 # 합성본 verify 도 실패
    assert out["gate_passed"] is None                   # 합성본은 원본 물건 픽셀 → 미검증
    assert out["detect_failed"] is False
    assert len(vcalls) == 2 * pipeline_mod.VERIFY_ATTEMPTS
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["verify_failed"] is True and ins["composite_reason"] == "verify_failed"
    assert ins.get("gate_retried") in (None, False)


def test_graph_verify_fails_on_generate_then_ok_on_composite(monkeypatch, make_png):
    """생성본 verify 실패 → 배경 교체 → 합성본 verify 성공: verify_failed 는 최신값(False)."""
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    _verify_raises(monkeypatch, [RuntimeError("a"), RuntimeError("b"),
                                 [{"what": "얼룩", "preserved": True}]])

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert calls["gen_seeds"] == [None]
    assert out["mode"] == "composite" and out["composite_reason"] == "verify_failed"
    assert out["verify_failed"] is False and out["gate_passed"] is True


def test_graph_verify_first_raises_then_succeeds_takes_normal_path(monkeypatch, make_png,
                                                                     no_detect_sleep):
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    vcalls = _verify_raises(monkeypatch, [TimeoutError("t/o"),
                                          [{"what": "얼룩", "preserved": True}]])

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert calls["gen_seeds"] == [None] and calls["compose"] == 0
    assert out["mode"] == "generate" and out["composite_reason"] is None
    assert out["verify_failed"] is False and out["gate_passed"] is True
    assert len(vcalls) == 2
    assert pipeline_mod.DETECT_RETRY_DELAY_S in no_detect_sleep


def test_graph_verify_bad_json_goes_composite_not_regen(monkeypatch, make_png):
    """strict verify: _call 이 {} 이면 예전엔 '전부 사라짐' → 재생성이었지만 이제
    verify_failed → 바로 배경 교체."""
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate",
                        _ORIG_VERIFY_AND_LOCATE)
    monkeypatch.setattr(pipeline_mod.detector, "_call", lambda img, prompt, name="": {})

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert calls["gen_seeds"] == [None]
    assert out["mode"] == "composite" and out["composite_reason"] == "verify_failed"
    assert out["verify_failed"] is True


def test_graph_verify_fail_with_composite_failure_keeps_unverified_generate(monkeypatch, make_png):
    """verify 실패 → 배경 교체마저 실패 → 생성본을 유지하되 '검증 못 함'으로 드러낸다
    (gate False + verify_failed True, 재생성·재검사 없음). 오리기 실패 반환에는
    composite_reason 이 없어 None (gate_failed 경로와 같은 기존 동작)."""
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    vcalls = _verify_raises(monkeypatch, [RuntimeError("x")] * 8)
    _compose_fails(monkeypatch, calls)

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert calls["gen_seeds"] == [None] and calls["compose"] == 1
    assert out["mode"] == "composite_failed" and out["status"] == "pass"
    assert out["gate_passed"] is False and out["verify_failed"] is True
    assert out["composite_reason"] is None
    assert len(vcalls) == pipeline_mod.VERIFY_ATTEMPTS


@pytest.mark.parametrize("state,expected", [
    ({"verify_failed": True}, "verify_failed"),
    ({"verify_failed": True, "detect_failed": True}, "detect_failed"),
    ({"verify_failed": True, "composite_reason": "guard_failed"}, "guard_failed"),
    ({}, "gate_failed"),
    ({"verify_failed": False}, "gate_failed"),
])
def test_composite_reason_default_priority(monkeypatch, state, expected):
    _compose_ok(monkeypatch)
    out = pipeline_mod.composite(_comp_state(**state))
    assert out["composite_reason"] == expected


def test_run_transform_with_result_includes_verify_failed_and_mode(monkeypatch, make_png):
    """dev 그래프는 재생성·배경 교체 루프가 없다 — verify 실패가 결과에 그대로 드러난다."""
    _patch_graph_deps(monkeypatch, make_png, [[]])
    vcalls = _verify_raises(monkeypatch, [RuntimeError("x")] * 2)

    out = pipeline_mod.run_transform_with_result("fid-g", "studio_white",
                                                 make_png(color=GEN_COLOR))

    assert out["verify_failed"] is True and out["gate_passed"] is False
    assert out["mode"] == "generate"
    assert out["detect_failed"] is False and out["status"] == "pass"
    assert len(vcalls) == pipeline_mod.VERIFY_ATTEMPTS


def test_run_transform_obs_output_includes_verify_failed(monkeypatch):
    fake_lf = _enable_fake_langfuse(monkeypatch)
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(_graph_out(verify_failed=True)),
                        raising=False)

    out = pipeline_mod.run_transform("fid2", "preset_b")

    assert out["verify_failed"] is True
    outputs = [c["output"] for c in fake_lf.obs.update_calls if "output" in c]
    assert any(o.get("verify_failed") is True for o in outputs)


# ══ item_dino·item_patch: _item_pair / _item_signals / _item_check ══
import time  # noqa: E402  (마감 계산용 — pipeline 의 time 은 픽스처가 바꿔 끼운다)


def _isolate_spy(monkeypatch, fail_on=None):
    """compositor.isolate 가짜 — 호출 기록, fail_on("original"/"result") 이면 거기서 raise."""
    seen = []

    def fake(image_bytes, item_box=None, original=False):
        seen.append((image_bytes, item_box, original))
        which = "original" if original else "result"
        if which == fail_on:
            raise ValueError("물건을 찾지 못함 (알파가 비어 있음)")
        return b"ISO_" + which.encode()
    monkeypatch.setattr(pipeline_mod.compositor, "isolate", fake)
    return seen


def _patch_ok(value=0.97):
    return GuardResult(name="item_patch", passed=value >= 0.95, value=value,
                       threshold=0.95, severity="soft")


def _stub_item_guards(monkeypatch, item=None, patch=None):
    """item_guard / item_patch_guard 가짜 — 받은 pair 를 기록한다. 기본은 둘 다 None(생략)."""
    seen = {"item": [], "patch": []}
    monkeypatch.setattr(pipeline_mod.guards, "item_guard",
                        lambda pair: seen["item"].append(pair) or item)
    monkeypatch.setattr(pipeline_mod.guards, "item_patch_guard",
                        lambda pair: seen["patch"].append(pair) or patch)
    return seen


def _deadline(s=30):
    return time.monotonic() + s


def test_old_product_names_are_gone():
    assert not hasattr(pipeline_mod, "_product_pair")
    assert "product_similarity" not in pipeline_mod.State.__annotations__
    assert "item_similarity" in pipeline_mod.State.__annotations__


def test_item_pair_isolates_original_with_box_and_result_without(monkeypatch):
    seen = _isolate_spy(monkeypatch)
    box = {"x1": 1, "y1": 2, "x2": 3, "y2": 4}
    pair = pipeline_mod._item_pair({"original": b"O", "result": b"R", "item_box": box})
    assert pair == (b"ISO_original", b"ISO_result")
    assert seen == [(b"O", box, True), (b"R", None, False)]


def test_item_pair_no_item_box_passes_none(monkeypatch):
    seen = _isolate_spy(monkeypatch)
    pipeline_mod._item_pair({"original": b"O", "result": b"R"})
    assert seen[0] == (b"O", None, True)


@pytest.mark.parametrize("fail_on", ["original", "result"])
def test_item_pair_isolate_failure_returns_none(monkeypatch, fail_on):
    _isolate_spy(monkeypatch, fail_on=fail_on)
    assert pipeline_mod._item_pair({"original": b"O", "result": b"R"}) is None


def test_item_pair_missing_state_key_returns_none(monkeypatch):
    _isolate_spy(monkeypatch)
    assert pipeline_mod._item_pair({"original": b"O"}) is None


def test_item_pair_real_isolate_with_blocked_cutout_returns_none():
    """conftest 가 오리기 모델을 막아 둔 상태 — 실제 isolate 가 터져도 None."""
    assert pipeline_mod._item_pair({"original": ORIG_PNG, "result": GEN_PNG}) is None


# ── _item_signals: 백그라운드에서 쌍 → 두 가드 ──
def test_item_signals_feeds_same_pair_to_both_guards(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "_item_pair", lambda s: (b"OI", b"RI"))
    seen = _stub_item_guards(monkeypatch, _item_ok(0.9), _patch_ok(0.97))
    g, p = pipeline_mod._item_signals({})
    assert (g.name, p.name) == ("item_dino", "item_patch")
    assert seen == {"item": [(b"OI", b"RI")], "patch": [(b"OI", b"RI")]}


def test_item_signals_cutout_failure_passes_none_to_both(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "_item_pair", lambda s: None)
    seen = _stub_item_guards(monkeypatch)
    assert pipeline_mod._item_signals({}) == (None, None)
    assert seen == {"item": [None], "patch": [None]}


def test_item_signals_real_guards_use_item_span_name_and_patch(monkeypatch):
    calls = []
    monkeypatch.setattr(pipeline_mod, "_item_pair", lambda s: (b"OI", b"RI"))
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity",
                        lambda o, r, **kw: calls.append((o, r, kw)) or 0.7)
    monkeypatch.setattr(pipeline_mod.embedder, "patch_similarity",
                        lambda o, r: calls.append(("patch", o, r)) or 0.99)
    g, p = pipeline_mod._item_signals({})
    assert (g.value, g.passed, p.value, p.passed) == (0.7, False, 0.99, True)
    assert calls == [(b"OI", b"RI", {"name": "item_dino_similarity"}), ("patch", b"OI", b"RI")]


def test_item_signals_real_patch_guard_exception_is_swallowed(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "_item_pair", lambda s: (b"a", b"b"))
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", lambda o, r, **kw: 0.9)

    def boom(o, r):
        raise ValueError("물건 패치가 없음")
    monkeypatch.setattr(pipeline_mod.embedder, "patch_similarity", boom)
    g, p = pipeline_mod._item_signals({})
    assert g.value == 0.9 and p is None


# ── _remaining ──
def test_remaining_future_deadline_is_positive_and_bounded():
    r = pipeline_mod._remaining(time.monotonic() + 5)
    assert 4 < r <= 5


@pytest.mark.parametrize("ago", [0.001, 10, 1e6])
def test_remaining_past_deadline_is_zero_not_negative(ago):
    assert pipeline_mod._remaining(time.monotonic() - ago) == 0.0


# ── _item_check(fut, deadline) ──
def test_item_check_waits_remaining_time_and_returns_values():
    fut = _FakeFuture((_item_ok(0.9), _patch_ok(0.97)))
    assert pipeline_mod.ITEM_WAIT_S == 30
    assert pipeline_mod._item_check(fut, _deadline(30)) == ([], 0.9, 0.97)   # 통과면 기록 없음
    [t] = fut.result_timeouts
    assert 29 < t <= 30 and fut.cancelled == 0


def test_item_check_past_deadline_waits_zero():
    fut = _FakeFuture((None, None))
    pipeline_mod._item_check(fut, time.monotonic() - 5)
    assert fut.result_timeouts == [0.0]


def test_item_check_failed_item_dino_returns_record():
    fails, sim, patch = pipeline_mod._item_check(
        _FakeFuture((_item_ok(0.5), _patch_ok(0.97))), _deadline())
    assert (sim, patch) == (0.5, 0.97)
    assert fails == [{"name": "item_dino", "passed": False, "value": 0.5,
                      "threshold": 0.80, "severity": "soft"}]


def test_item_check_patch_fail_only_is_recorded():
    fails, sim, patch = pipeline_mod._item_check(
        _FakeFuture((_item_ok(0.9), _patch_ok(0.94))), _deadline())
    assert (sim, patch) == (0.9, 0.94)
    assert fails == [{"name": "item_patch", "passed": False, "value": 0.94,
                      "threshold": 0.95, "severity": "soft"}]


def test_item_check_both_fail_records_in_item_then_patch_order():
    fails, sim, patch = pipeline_mod._item_check(
        _FakeFuture((_item_ok(0.5), _patch_ok(0.3))), _deadline())
    assert [f["name"] for f in fails] == ["item_dino", "item_patch"]
    assert (sim, patch) == (0.5, 0.3)


@pytest.mark.parametrize("g,p,expected", [
    (None, _patch_ok(0.1), ([{"name": "item_patch", "passed": False, "value": 0.1,
                              "threshold": 0.95, "severity": "soft"}], None, 0.1)),
    (_item_ok(0.9), None, ([], 0.9, None)),
    (_item_ok(0.2), None, ([{"name": "item_dino", "passed": False, "value": 0.2,
                             "threshold": 0.80, "severity": "soft"}], 0.2, None)),
    (None, None, ([], None, None)),
])
def test_item_check_one_guard_omitted_other_still_reported(g, p, expected):
    assert pipeline_mod._item_check(_FakeFuture((g, p)), _deadline()) == expected


def test_item_check_zero_values_are_kept_not_none():
    """값 0.0 도 None 과 구분된다 (falsy 값 처리)."""
    fails, sim, patch = pipeline_mod._item_check(
        _FakeFuture((_item_ok(0.0), _patch_ok(0.0))), _deadline())
    assert sim == 0.0 and patch == 0.0 and len(fails) == 2


@pytest.mark.parametrize("exc", [RuntimeError("boom"), TimeoutError()])
def test_item_check_future_failure_or_timeout_cancels_and_gives_none(exc):
    fut = _FakeFuture(exc=exc)
    assert pipeline_mod._item_check(fut, _deadline()) == ([], None, None)
    assert fut.cancelled == 1


@pytest.mark.parametrize("bad", [None, (None,), (None, None, None)])
def test_item_check_unpackable_future_value_gives_none(bad):
    """(g, p) 로 풀리지 않는 값 — 예외로 삼키고 생략."""
    fut = _FakeFuture(bad)
    assert pipeline_mod._item_check(fut, _deadline()) == ([], None, None)
    assert fut.cancelled == 1


# ── ocr_local: _local_ocr_check(fut, deadline) ──
def _ocr_ok(value=1.0):
    return GuardResult(name="ocr_local", passed=value >= 0.95, value=value,
                       threshold=0.95, severity="soft")


def test_local_ocr_wait_constant():
    assert pipeline_mod.LOCAL_OCR_WAIT_S == 60


def test_local_ocr_check_none_future_is_none_and_skips_guard(monkeypatch):
    def poison(*a):
        raise AssertionError("꺼져 있으면 가드를 부르지 않는다")
    monkeypatch.setattr(pipeline_mod.guards, "local_ocr_guard", poison)
    assert pipeline_mod._local_ocr_check(None, _deadline(60)) == (None, None)


@pytest.mark.parametrize("exc", [RuntimeError("easyocr 없음"), TimeoutError(),
                                 ModuleNotFoundError("No module named 'easyocr'")])
def test_local_ocr_check_future_exception_cancels_and_gives_none(monkeypatch, exc):
    def poison(*a):
        raise AssertionError("대기 실패면 가드를 부르지 않는다")
    monkeypatch.setattr(pipeline_mod.guards, "local_ocr_guard", poison)
    fut = _FakeFuture(exc=exc)
    assert pipeline_mod._local_ocr_check(fut, _deadline(60)) == (None, None)
    assert fut.cancelled == 1
    [t] = fut.result_timeouts
    assert 59 < t <= 60


def test_local_ocr_check_past_deadline_waits_zero(monkeypatch):
    monkeypatch.setattr(pipeline_mod.guards, "local_ocr_guard", lambda b, a: None)
    fut = _FakeFuture(([], []))
    pipeline_mod._local_ocr_check(fut, time.monotonic() - 1)
    assert fut.result_timeouts == [0.0]


def test_local_ocr_check_guard_exception_gives_none(monkeypatch):
    def boom(before, after):
        raise ValueError("metric 실패")
    monkeypatch.setattr(pipeline_mod.guards, "local_ocr_guard", boom)
    fut = _FakeFuture((["A"], ["A"]))
    assert pipeline_mod._local_ocr_check(fut, _deadline(60)) == (None, None)
    assert fut.cancelled == 1   # 이미 끝난 future 라 무해


def test_local_ocr_check_passes_before_after_to_guard(monkeypatch):
    seen = []
    monkeypatch.setattr(pipeline_mod.guards, "local_ocr_guard",
                        lambda before, after: seen.append((before, after)) or _ocr_ok(1.0))
    fut = _FakeFuture((["NIKE"], ["NIKE"]))
    assert pipeline_mod._local_ocr_check(fut, _deadline(60)) == (None, 1.0)
    assert seen == [(["NIKE"], ["NIKE"])] and fut.cancelled == 0


def test_local_ocr_check_fail_returns_record_and_recall(monkeypatch):
    monkeypatch.setattr(pipeline_mod.guards, "local_ocr_guard", lambda b, a: _ocr_ok(0.5))
    rec, recall = pipeline_mod._local_ocr_check(_FakeFuture((["A"], [])), _deadline(60))
    assert recall == 0.5
    assert rec == {"name": "ocr_local", "passed": False, "value": 0.5,
                   "threshold": 0.95, "severity": "soft"}


def test_local_ocr_check_guard_omitted_gives_none(monkeypatch):
    """원본에서 읽은 글자 없음 → 가드 None → (None, None)."""
    monkeypatch.setattr(pipeline_mod.guards, "local_ocr_guard", lambda b, a: None)
    assert pipeline_mod._local_ocr_check(_FakeFuture(([], ["X"])), _deadline(60)) == (None, None)


def test_local_ocr_check_real_guard_zero_recall():
    """실제 local_ocr_guard + metric: 글자가 전부 사라지면 recall 0 으로 기록."""
    rec, recall = pipeline_mod._local_ocr_check(_FakeFuture((["NIKE"], [])), _deadline(60))
    assert recall == 0.0 and rec["name"] == "ocr_local" and rec["passed"] is False


def test_local_ocr_lines_reads_original_with_box_and_result_whole(monkeypatch):
    from app.services.ai import local_ocr
    seen = []
    monkeypatch.setattr(local_ocr, "read_original",
                        lambda b, box=None: seen.append(("orig", b, box)) or ["A"])
    monkeypatch.setattr(local_ocr, "read_lines",
                        lambda b, box=None: seen.append(("res", b, box)) or ["B"])
    box = {"x1": 1, "y1": 2, "x2": 3, "y2": 4}
    out = pipeline_mod._local_ocr_lines({"original": b"O", "result": b"R", "item_box": box})
    assert out == (["A"], ["B"])
    assert seen == [("orig", b"O", box), ("res", b"R", None)]


# ══ validate_result: 누끼·로컬 OCR (통과한 시도에서만, 별도 풀, 공통 마감) ══
def _spy_background_by_fn(monkeypatch, futures):
    """_in_background 가짜 — 제출 fn 이름으로 Future 를 고른다. 없으면 _item_signals 엔
    (None, None), 나머지엔 None 을 돌려주는 가짜. 제출 기록 (fn 이름, args, kwargs) 반환."""
    submitted = []

    def fake(fn, *a, **kw):
        submitted.append((fn.__name__, a, kw))
        if fn.__name__ in futures:
            return futures[fn.__name__]
        return _FakeFuture((None, None) if fn.__name__ == "_item_signals" else None)
    monkeypatch.setattr(pipeline_mod, "_in_background", fake)
    return submitted


def _no_vlm_ocr_pair(monkeypatch):
    """item_texts 가 있으면 _ocr_pair 가 결과 글자를 VLM 으로 읽는다 — 여기선 비교할 게 없다고."""
    monkeypatch.setattr(pipeline_mod, "_ocr_pair", lambda s: ([], [], []))


def _photo_ok():
    return _FakeFuture({"valid": True, "reason": ""})


def test_validate_result_pass_submits_item_signals_on_cutout_pool(monkeypatch):
    submitted = _spy_background(monkeypatch, _photo_ok(), _FakeFuture((_item_ok(), None)))
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])
    state = _validate_state()

    pipeline_mod.validate_result(state)

    assert submitted == [(pipeline_mod.detector.check_photo, (GEN_PNG,), {}),
                         (pipeline_mod._item_signals, (state,), {"pool": pipeline_mod._CUTOUT_POOL})]


def test_cutout_and_ocr_pools_are_separate_and_small():
    assert pipeline_mod._CUTOUT_POOL is not pipeline_mod._POOL
    assert pipeline_mod._CUTOUT_POOL._max_workers == 2
    assert pipeline_mod._OCR_POOL not in (pipeline_mod._POOL, pipeline_mod._CUTOUT_POOL)
    assert pipeline_mod._OCR_POOL._max_workers == 1


def test_in_background_pool_kwarg_selects_pool():
    import threading

    def name():
        return threading.current_thread().name
    cut = pipeline_mod._in_background(name, pool=pipeline_mod._CUTOUT_POOL).result(timeout=5)
    ocr = pipeline_mod._in_background(name, pool=pipeline_mod._OCR_POOL).result(timeout=5)
    default = pipeline_mod._in_background(name).result(timeout=5)
    assert cut.startswith("cutout") and ocr.startswith("ocr") and default.startswith("pipeline")


@pytest.mark.parametrize("seed", [None, 5])
def test_validate_result_block_never_submits_item_real_pool(monkeypatch, seed):
    """실제 _in_background: 막힌 시도(재시도/배경 교체 모두)엔 누끼 비교가 안 돈다."""
    ran = []
    monkeypatch.setattr(pipeline_mod, "_item_signals", lambda s: ran.append(s))
    monkeypatch.setattr(pipeline_mod, "_item_pair", lambda s: ran.append(s))
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_hard_fail()])
    out = pipeline_mod.validate_result(_validate_state(guard_seed=seed))
    assert "item_similarity" not in out and ran == []


def test_validate_result_pass_returns_item_similarity(monkeypatch):
    _spy_background(monkeypatch, _photo_ok(), _FakeFuture((_item_ok(0.88), _patch_ok(0.97))))
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_dino_ok(0.83)])
    out = pipeline_mod.validate_result(_validate_state())
    assert out["status"] == "pass" and out["guard_report"] == []
    assert out["visual_similarity"] == 0.83 and out["item_similarity"] == 0.88
    assert out["item_patch_similarity"] == 0.97


def test_validate_result_failed_item_dino_appended_to_guard_report(monkeypatch):
    _spy_background(monkeypatch, _photo_ok(), _FakeFuture((_item_ok(0.5), None)))
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_soft_fail()])
    out = pipeline_mod.validate_result(_validate_state())
    assert out["status"] == "pass"                       # soft — 판정 영향 없음
    assert [g["name"] for g in out["guard_report"]] == ["dino_band", "item_dino"]
    assert out["guard_report"][1] == {"name": "item_dino", "passed": False, "value": 0.5,
                                      "threshold": 0.80, "severity": "soft"}
    assert out["item_similarity"] == 0.5 and out["item_patch_similarity"] is None


def test_validate_result_item_timeout_gives_none_cancels_and_verdict_unaffected(monkeypatch):
    item = _FakeFuture(exc=TimeoutError())
    _spy_background(monkeypatch, _photo_ok(), item)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_dino_ok(0.9)])
    out = pipeline_mod.validate_result(_validate_state())
    [t] = item.result_timeouts
    assert 0 < t <= pipeline_mod.ITEM_WAIT_S and item.cancelled == 1
    assert out["status"] == "pass" and out["guard_retry"] is False
    assert out["item_similarity"] is None and out["item_patch_similarity"] is None
    assert out["guard_report"] == [] and out["visual_similarity"] == 0.9
    assert out["photo_check"] == {"valid": True, "reason": ""}
    assert storage.load("result", "v_p.jpg") is not None


def test_validate_result_item_real_timeout_does_not_block(monkeypatch):
    """실제 풀 + 느린 누끼: ITEM_WAIT_S 를 넘기면 기다리지 않고 None."""
    import threading
    release = threading.Event()

    def slow(s):
        release.wait(5)
        return (_item_ok(), None)
    monkeypatch.setattr(pipeline_mod, "_item_signals", slow)
    monkeypatch.setattr(pipeline_mod, "ITEM_WAIT_S", 0.05)
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": True, "reason": ""})
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])
    try:
        out = pipeline_mod.validate_result(_validate_state())
    finally:
        release.set()
    assert out["status"] == "pass" and out["item_similarity"] is None


def _fake_clock(monkeypatch, start=100.0):
    """pipeline 의 time.monotonic 을 손으로 움직이는 시계로. clock[0] 을 바꾸면 시간이 흐른다."""
    clock = [start]
    monkeypatch.setattr(pipeline_mod, "time", types.SimpleNamespace(
        time=time.time, sleep=lambda s: None, monotonic=lambda: clock[0]))
    return clock


def test_validate_result_deadlines_start_before_photo_wait(monkeypatch):
    """마감은 작업을 넘긴 시점부터 — check_photo 대기 시간만큼 누끼·OCR 대기가 줄어든다."""
    clock = _fake_clock(monkeypatch)
    monkeypatch.setattr(settings, "local_ocr_guard", True)

    class SlowPhoto(_FakeFuture):
        def result(self, timeout=None):
            clock[0] += 12
            return {"valid": True, "reason": ""}
    item, ocr = _FakeFuture((None, None)), _FakeFuture(([], []))
    _spy_background_by_fn(monkeypatch, {"check_photo": SlowPhoto(),
                                        "_item_signals": item, "_local_ocr_lines": ocr})
    _no_vlm_ocr_pair(monkeypatch)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])

    pipeline_mod.validate_result(_validate_state(item_texts=[{"text": "NIKE"}]))

    assert item.result_timeouts == [pipeline_mod.ITEM_WAIT_S - 12]
    assert ocr.result_timeouts == [pipeline_mod.LOCAL_OCR_WAIT_S - 12]


def test_validate_result_photo_wait_longer_than_item_wait_gives_zero_timeout(monkeypatch):
    clock = _fake_clock(monkeypatch)

    class VerySlowPhoto(_FakeFuture):
        def result(self, timeout=None):
            clock[0] += pipeline_mod.ITEM_WAIT_S + 5
            return {"valid": True, "reason": ""}
    item = _FakeFuture((_item_ok(0.9), None))
    _spy_background_by_fn(monkeypatch, {"check_photo": VerySlowPhoto(), "_item_signals": item})
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])
    out = pipeline_mod.validate_result(_validate_state())
    assert item.result_timeouts == [0.0]
    assert out["item_similarity"] == 0.9     # 이미 끝나 있으면 0초 대기로도 값을 받는다


def test_validate_result_photo_timeout_still_checks_item(monkeypatch):
    _spy_background(monkeypatch, _FakeFuture(exc=TimeoutError()),
                    _FakeFuture((_item_ok(0.81), None)))
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])
    out = pipeline_mod.validate_result(_validate_state())
    assert out["photo_check"] == {"valid": True, "reason": ""}
    assert out["item_similarity"] == 0.81


def test_validate_result_item_submitted_before_save_and_photo_wait(monkeypatch):
    order = []

    class Photo(_FakeFuture):
        def result(self, timeout=None):
            order.append("photo_wait")
            return {"valid": True, "reason": ""}

    class Item(_FakeFuture):
        def result(self, timeout=None):
            order.append("item_wait")
            return (None, None)

    def fake_bg(fn, *a, **kw):
        order.append(("submit", fn.__name__))
        return Item() if fn is pipeline_mod._item_signals else Photo()
    monkeypatch.setattr(pipeline_mod, "_in_background", fake_bg)
    real_save = pipeline_mod.storage.save

    def save(*a, **k):
        order.append("save")
        return real_save(*a, **k)
    monkeypatch.setattr(pipeline_mod.storage, "save", save)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])

    pipeline_mod.validate_result(_validate_state())

    assert order == [("submit", "check_photo"), ("submit", "_item_signals"),
                     "save", "photo_wait", "item_wait"]


@pytest.mark.parametrize("ocr_on", [False, True])
def test_validate_result_save_failure_cancels_all_background_and_reraises(monkeypatch, ocr_on):
    monkeypatch.setattr(settings, "local_ocr_guard", ocr_on)
    photo, item, ocr = _photo_ok(), _FakeFuture((None, None)), _FakeFuture(([], []))
    _spy_background_by_fn(monkeypatch, {"check_photo": photo, "_item_signals": item,
                                        "_local_ocr_lines": ocr})
    _no_vlm_ocr_pair(monkeypatch)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])

    def boom(*a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(pipeline_mod.storage, "save", boom)

    with pytest.raises(OSError):
        pipeline_mod.validate_result(_validate_state(item_texts=[{"text": "NIKE"}]))

    assert photo.cancelled == 1 and item.cancelled == 1
    assert ocr.cancelled == (1 if ocr_on else 0)
    assert photo.result_timeouts == item.result_timeouts == ocr.result_timeouts == []


def test_validate_result_save_keyboard_interrupt_also_cancels(monkeypatch):
    photo, item = _photo_ok(), _FakeFuture((None, None))
    _spy_background_by_fn(monkeypatch, {"check_photo": photo, "_item_signals": item})
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])

    def interrupt(*a, **k):
        raise KeyboardInterrupt
    monkeypatch.setattr(pipeline_mod.storage, "save", interrupt)
    with pytest.raises(KeyboardInterrupt):
        pipeline_mod.validate_result(_validate_state())
    assert photo.cancelled == 1 and item.cancelled == 1


def test_validate_result_real_pool_runs_item_signals_with_state(monkeypatch):
    seen = _isolate_spy(monkeypatch)
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": True, "reason": ""})
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])
    got = _stub_item_guards(monkeypatch, _item_ok(0.93), _patch_ok(0.96))

    out = pipeline_mod.validate_result(_validate_state(item_box={"x1": 5}))

    assert got == {"item": [(b"ISO_original", b"ISO_result")],
                   "patch": [(b"ISO_original", b"ISO_result")]}
    assert seen == [(ORIG_PNG, {"x1": 5}, True), (GEN_PNG, None, False)]
    assert out["item_similarity"] == 0.93 and out["item_patch_similarity"] == 0.96


def test_validate_result_real_pool_item_signals_crash_gives_none(monkeypatch):
    def boom(s):
        raise RuntimeError("isolate thread died")
    monkeypatch.setattr(pipeline_mod, "_item_signals", boom)
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": True, "reason": ""})
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])
    out = pipeline_mod.validate_result(_validate_state())
    assert out["status"] == "pass" and out["item_similarity"] is None
    assert out["item_patch_similarity"] is None


# ── 로컬 OCR: 켜짐·글자 있음일 때만 ──
@pytest.mark.parametrize("enabled,texts", [
    (False, [{"text": "NIKE"}]),   # 설정 꺼짐
    (True, None),                  # 글자 없음
    (True, []),
    (False, None),
])
def test_validate_result_local_ocr_not_started_when_off_or_no_texts(monkeypatch, enabled, texts):
    monkeypatch.setattr(settings, "local_ocr_guard", enabled)
    submitted = _spy_background_by_fn(monkeypatch, {"check_photo": _photo_ok()})
    _no_vlm_ocr_pair(monkeypatch)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])

    def poison(*a):
        raise AssertionError("로컬 OCR 이 꺼져 있으면 가드도 안 부른다")
    monkeypatch.setattr(pipeline_mod.guards, "local_ocr_guard", poison)
    state = _validate_state() if texts is None else _validate_state(item_texts=texts)

    out = pipeline_mod.validate_result(state)

    assert [n for n, _, _ in submitted] == ["check_photo", "_item_signals"]
    assert "ocr_local_recall" in out and out["ocr_local_recall"] is None
    assert out["guard_report"] == []


def test_local_ocr_guard_setting_default_off():
    from app.core.config import Settings
    assert Settings.model_fields["local_ocr_guard"].default is False


def test_validate_result_local_ocr_started_on_ocr_pool_when_on(monkeypatch):
    monkeypatch.setattr(settings, "local_ocr_guard", True)
    submitted = _spy_background_by_fn(monkeypatch, {
        "check_photo": _photo_ok(), "_local_ocr_lines": _FakeFuture((["NIKE"], ["NIKE"]))})
    _no_vlm_ocr_pair(monkeypatch)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])
    state = _validate_state(item_texts=[{"text": "NIKE"}])

    out = pipeline_mod.validate_result(state)

    assert [n for n, _, _ in submitted] == ["check_photo", "_item_signals", "_local_ocr_lines"]
    assert submitted[1][2] == {"pool": pipeline_mod._CUTOUT_POOL}
    assert submitted[2] == ("_local_ocr_lines", (state,), {"pool": pipeline_mod._OCR_POOL})
    assert out["ocr_local_recall"] == 1.0 and out["guard_report"] == []


def test_validate_result_block_never_starts_local_ocr(monkeypatch):
    monkeypatch.setattr(settings, "local_ocr_guard", True)
    submitted = _spy_background_by_fn(monkeypatch, {})
    _no_vlm_ocr_pair(monkeypatch)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_hard_fail()])
    out = pipeline_mod.validate_result(_validate_state(item_texts=[{"text": "NIKE"}]))
    assert [n for n, _, _ in submitted] == ["check_photo"]
    assert "ocr_local_recall" not in out


def test_validate_result_report_order_run_item_patch_then_ocr(monkeypatch):
    """soft 실패 기록 순서: 출력 가드 → item_dino → item_patch → ocr_local. 판정은 pass."""
    monkeypatch.setattr(settings, "local_ocr_guard", True)
    _spy_background_by_fn(monkeypatch, {
        "check_photo": _photo_ok(),
        "_item_signals": _FakeFuture((_item_ok(0.5), _patch_ok(0.6))),
        "_local_ocr_lines": _FakeFuture((["NIKE"], []))})
    _no_vlm_ocr_pair(monkeypatch)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_soft_fail()])

    out = pipeline_mod.validate_result(_validate_state(item_texts=[{"text": "NIKE"}]))

    assert out["status"] == "pass" and out["guard_retry"] is False
    assert [g["name"] for g in out["guard_report"]] == [
        "dino_band", "item_dino", "item_patch", "ocr_local"]
    assert (out["item_similarity"], out["item_patch_similarity"], out["ocr_local_recall"]) \
        == (0.5, 0.6, 0.0)


def test_validate_result_local_ocr_failure_cancels_and_does_not_affect_verdict(monkeypatch):
    monkeypatch.setattr(settings, "local_ocr_guard", True)
    ocr = _FakeFuture(exc=ModuleNotFoundError("easyocr"))
    _spy_background_by_fn(monkeypatch, {"check_photo": _photo_ok(), "_local_ocr_lines": ocr})
    _no_vlm_ocr_pair(monkeypatch)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [_dino_ok(0.9)])
    out = pipeline_mod.validate_result(_validate_state(item_texts=[{"text": "NIKE"}]))
    assert out["status"] == "pass" and out["guard_report"] == []
    assert out["ocr_local_recall"] is None and out["visual_similarity"] == 0.9
    assert ocr.cancelled == 1


def test_validate_result_real_pool_local_ocr_with_stubbed_reader(monkeypatch):
    """실제 풀 + 실제 _local_ocr_lines, local_ocr._load 만 가짜 — easyocr 없이 끝까지."""
    from collections import OrderedDict
    from app.services.ai import local_ocr
    monkeypatch.setattr(local_ocr, "_ORIG_CACHE", OrderedDict())

    class Reader:
        def readtext(self, png):
            return [(None, "NIKE", 0.9)]
    monkeypatch.setattr(local_ocr, "_load", lambda: Reader())
    monkeypatch.setattr(settings, "local_ocr_guard", True)
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": True, "reason": ""})
    _no_vlm_ocr_pair(monkeypatch)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", lambda *a: [])
    _stub_item_guards(monkeypatch)
    out = pipeline_mod.validate_result(_validate_state(item_texts=[{"text": "NIKE"}]))
    assert out["ocr_local_recall"] == 1.0 and out["guard_report"] == []


# ── 초기화 · State · inspect ──
def test_generate_resets_item_patch_and_ocr_local(monkeypatch):
    monkeypatch.setattr(pipeline_mod, "_generate_ai", lambda o, p, seed=None: b"NEW")
    out = pipeline_mod.generate({"original": b"O", "preset": {"prompt": "P"},
                                 "file_id": "f", "preset_key": "p",
                                 "item_patch_similarity": 0.9, "ocr_local_recall": 1.0})
    assert "item_patch_similarity" in out and out["item_patch_similarity"] is None
    assert "ocr_local_recall" in out and out["ocr_local_recall"] is None


def test_composite_resets_item_patch_and_ocr_local(monkeypatch):
    _compose_ok(monkeypatch)
    out = pipeline_mod.composite(_comp_state(item_patch_similarity=0.9, ocr_local_recall=1.0))
    assert "item_patch_similarity" in out and out["item_patch_similarity"] is None
    assert "ocr_local_recall" in out and out["ocr_local_recall"] is None


def test_state_declares_new_fields():
    ann = pipeline_mod.State.__annotations__
    assert "item_patch_similarity" in ann and "ocr_local_recall" in ann


def test_save_inspect_includes_item_patch_and_ocr_local():
    pipeline_mod.save_inspect({"file_id": "si", "preset_key": "p", "anchors": [],
                               "item_patch_similarity": 0.91, "ocr_local_recall": 0.5})
    ins = json.loads(storage.load("quality", "si_p_inspect.json"))
    assert ins["item_patch_similarity"] == 0.91 and ins["ocr_local_recall"] == 0.5


def test_save_inspect_new_fields_default_none():
    pipeline_mod.save_inspect({"file_id": "si", "preset_key": "p", "anchors": []})
    ins = json.loads(storage.load("quality", "si_p_inspect.json"))
    assert "item_patch_similarity" in ins and ins["item_patch_similarity"] is None
    assert "ocr_local_recall" in ins and ins["ocr_local_recall"] is None


# ── 그래프 끝까지 ──
def test_item_similarity_written_to_inspect_json_and_result(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png, [])
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", _ORIG_RUN_OUTPUT_GUARDS)
    _isolate_spy(monkeypatch)
    names = []

    def cos(o, r, name="dino_similarity"):
        calls["cosine"] += 1
        names.append(name)
        return 0.82 if o == b"ISO_original" else 0.9
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity", cos)
    patches = []
    monkeypatch.setattr(pipeline_mod.embedder, "patch_similarity",
                        lambda o, r: patches.append((o, r)) or 0.97)

    result = pipeline_mod.run_transform("fid-g", "studio_white")

    assert result["status"] == "pass"
    assert result["visual_similarity"] == 0.9 and result["item_similarity"] == 0.82
    assert sorted(names) == ["dino_similarity", "item_dino_similarity"]
    assert patches == [(b"ISO_original", b"ISO_result")]
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["item_similarity"] == 0.82 and ins["visual_similarity"] == 0.9
    assert ins["item_patch_similarity"] == 0.97 and ins["ocr_local_recall"] is None
    assert "product_similarity" not in ins
    assert ins["guard_report"] == []


def test_failed_item_dino_written_to_inspect_guard_report(monkeypatch, make_png):
    _patch_graph_deps(monkeypatch, make_png, [[_dino_ok(0.9)]])
    monkeypatch.setattr(pipeline_mod, "_item_pair", lambda s: (b"a", b"b"))
    _stub_item_guards(monkeypatch, _item_ok(0.4), _patch_ok(0.97))
    result = pipeline_mod.run_transform("fid-g", "studio_white")
    assert result["status"] == "pass" and result["item_similarity"] == 0.4
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert [g["name"] for g in ins["guard_report"]] == ["item_dino"]


def test_item_patch_similarity_written_to_inspect_json_end_to_end(monkeypatch, make_png):
    _patch_graph_deps(monkeypatch, make_png, [[_dino_ok(0.9)]])
    monkeypatch.setattr(pipeline_mod, "_item_pair", lambda s: (b"a", b"b"))
    _stub_item_guards(monkeypatch, _item_ok(0.9), _patch_ok(0.7))
    result = pipeline_mod.run_transform("fid-g", "studio_white")
    assert result["status"] == "pass"
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["item_patch_similarity"] == 0.7 and ins["ocr_local_recall"] is None
    assert [g["name"] for g in ins["guard_report"]] == ["item_patch"]


def test_item_similarity_none_in_inspect_json_after_composite(monkeypatch, make_png):
    """가드 두 번 불합격 → 배경 교체: 누끼 비교는 한 번도 안 돌고 값은 None."""
    _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], [_hard_fail()]])
    ran = []
    monkeypatch.setattr(pipeline_mod, "_item_pair", lambda s: ran.append(1))
    result = pipeline_mod.run_transform("fid-g", "studio_white")
    assert result["mode"] == "composite" and result["item_similarity"] is None
    assert ran == []
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert "item_similarity" in ins and ins["item_similarity"] is None
    assert ins["item_patch_similarity"] is None


def test_item_similarity_from_last_attempt_after_photo_regenerate(monkeypatch, make_png):
    """1회차 0.9 → photo invalid 재생성 → 2회차 누끼 실패(None): 이전 값이 남지 않는다."""
    monkeypatch.setattr(settings, "max_generate_attempts", 2, raising=False)
    _patch_graph_deps(monkeypatch, make_png, [[], []])
    pairs = iter([(b"a", b"b"), None])
    monkeypatch.setattr(pipeline_mod, "_item_pair", lambda s: next(pairs))
    monkeypatch.setattr(pipeline_mod.guards, "item_guard",
                        lambda pair: _item_ok(0.9) if pair else None)
    monkeypatch.setattr(pipeline_mod.guards, "item_patch_guard",
                        lambda pair: _patch_ok(0.97) if pair else None)
    verdicts = iter([{"valid": False, "reason": "cropped"}, {"valid": True, "reason": ""}])
    monkeypatch.setattr(pipeline_mod.detector, "check_photo", lambda img: next(verdicts))
    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})
    assert out["gen_attempts"] == 2
    assert out["item_similarity"] is None and out["item_patch_similarity"] is None


# ══ text_level: detect 가 글자 수준을 보고 글자 읽기·배경 교체를 정한다 ══
_B_DETECT = {"x1": 100, "y1": 100, "x2": 600, "y2": 700}
_B_READ = {"x1": 50, "y1": 50, "x2": 950, "y2": 950}


def test_state_declares_text_level():
    assert "text_level" in pipeline_mod.State.__annotations__


@pytest.mark.parametrize("level,box", [("dense", _B_DETECT), ("none", None), ("simple", _B_DETECT)])
def test_detect_node_passes_text_level_and_item_box(monkeypatch, level, box):
    anchors = [{"category": "print", "what": "logo", "where": "front"}]
    _detect_seq(monkeypatch, [{"anchors": anchors, "text_level": level, "item_box": box}])
    out = pipeline_mod.detect({"original": b"x"})
    assert out == {"anchors": anchors, "detect_failed": False, "text_level": level,
                   "item_box": box}


# ── read_text 노드 단위 ──
def _read_stub(monkeypatch, out=None, exc=None):
    seen = []

    def fake(img, item, **kw):
        seen.append({"img": img, "item": item, **kw})
        if exc is not None:
            raise exc
        return out
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text", fake)
    return seen


def test_read_text_lock_off_no_vlm_keeps_detect_box(monkeypatch):
    seen = _read_stub(monkeypatch, {"item_box": _B_READ, "texts": _texts(1)})
    s = {"original": b"o", "item_box": _B_DETECT}
    assert pipeline_mod.read_text(s) == {"item_texts": [], "item_box": _B_DETECT}
    assert pipeline_mod.read_text({"original": b"o"}) == {"item_texts": [], "item_box": None}
    assert seen == []


def test_read_text_keeps_detect_box_over_own(monkeypatch):
    monkeypatch.setattr(settings, "text_lock", True)
    _read_stub(monkeypatch, {"item_box": _B_READ, "texts": _texts(2)})
    out = pipeline_mod.read_text({"original": b"o", "item_box": _B_DETECT})
    assert out == {"item_texts": _texts(2), "item_box": _B_DETECT}


@pytest.mark.parametrize("state", [{"original": b"o"}, {"original": b"o", "item_box": None}])
def test_read_text_falls_back_to_own_box_when_detect_has_none(monkeypatch, state):
    monkeypatch.setattr(settings, "text_lock", True)
    _read_stub(monkeypatch, {"item_box": _B_READ, "texts": []})
    assert pipeline_mod.read_text(state) == {"item_texts": [], "item_box": _B_READ}


@pytest.mark.parametrize("read_out", [{"item_box": None, "texts": []}, {"texts": []}])
def test_read_text_no_box_anywhere_is_none(monkeypatch, read_out):
    monkeypatch.setattr(settings, "text_lock", True)
    _read_stub(monkeypatch, read_out)
    assert pipeline_mod.read_text({"original": b"o"})["item_box"] is None


@pytest.mark.parametrize("box", [_B_DETECT, None])
def test_read_text_failure_keeps_detect_box_and_no_texts(monkeypatch, capsys, box):
    monkeypatch.setattr(settings, "text_lock", True)
    _read_stub(monkeypatch, exc=RuntimeError("vlm down"))
    out = pipeline_mod.read_text({"original": b"o", "item_box": box})
    assert out == {"item_texts": [], "item_box": box}
    assert "composite_reason" not in out
    assert "[read_text]" in capsys.readouterr().out


def test_read_text_calls_non_strict_with_item(monkeypatch):
    monkeypatch.setattr(settings, "text_lock", True)
    seen = _read_stub(monkeypatch, {"item_box": None, "texts": []})
    pipeline_mod.read_text({"original": b"o"})
    pipeline_mod.read_text({"original": b"p", "item": "mug"})
    assert seen == [{"img": b"o", "item": "object"}, {"img": b"p", "item": "mug"}]


@pytest.mark.parametrize("min_texts,n,expected", [
    (12, 11, None), (12, 12, "text_heavy"), (12, 40, "text_heavy"),
    (0, 40, None),                     # 0 = 끔
    (1, 1, "text_heavy"), (1, 0, None),
])
def test_read_text_text_heavy_threshold(monkeypatch, min_texts, n, expected):
    monkeypatch.setattr(settings, "text_lock", True)
    monkeypatch.setattr(settings, "composite_first_min_texts", min_texts)
    _read_stub(monkeypatch, {"item_box": None, "texts": _texts(n)})
    out = pipeline_mod.read_text({"original": b"o"})
    assert out.get("composite_reason") == expected
    assert len(out["item_texts"]) == n


@pytest.mark.parametrize("extra", [
    {"provided_result": b"img"}, {"provided_result": b""},       # dev 그래프
    {"composite_error": "물건을 찾지 못함"},                        # 배경 교체가 이미 실패
])
def test_read_text_text_heavy_skipped_in_dev_or_after_composite_error(monkeypatch, extra):
    monkeypatch.setattr(settings, "text_lock", True)
    _read_stub(monkeypatch, {"item_box": None, "texts": _texts(40)})
    out = pipeline_mod.read_text({"original": b"o", **extra})
    assert "composite_reason" not in out and len(out["item_texts"]) == 40


def test_read_text_empty_composite_error_does_not_skip_text_heavy(monkeypatch):
    monkeypatch.setattr(settings, "text_lock", True)
    _read_stub(monkeypatch, {"item_box": None, "texts": _texts(12)})
    out = pipeline_mod.read_text({"original": b"o", "composite_error": None})
    assert out["composite_reason"] == "text_heavy"


# ── _route_after_composite: 생성 전 합성 실패 → 글자 아직 안 읽었으면 read_text ──
@pytest.mark.parametrize("state,expected", [
    ({"mode": "generate", "text_level": "dense"}, "read_text"),
    ({"mode": "generate", "text_level": None}, "read_text"),       # detect 실패
    ({"mode": "generate"}, "read_text"),
    ({"mode": "generate", "text_level": "simple"}, "read_text"),   # many_defects 로 갔던 simple
    ({"mode": "generate", "text_level": "none"}, "generate"),      # 글자 없음 확인됨
    ({"mode": "generate", "text_level": "dense", "item_texts": []}, "generate"),   # 이미 읽음
    ({"mode": "generate", "text_level": "dense", "item_texts": _texts(3)}, "generate"),
    ({"mode": "composite", "text_level": "dense"}, "ok"),
    ({"mode": "composite_failed", "text_level": "dense"}, "failed"),
    ({"text_level": "dense"}, "failed"),
])
def test_route_after_composite_text_lock_on(monkeypatch, state, expected):
    monkeypatch.setattr(settings, "text_lock", True)
    assert pipeline_mod._route_after_composite(state) == expected


@pytest.mark.parametrize("level", ["dense", None, "simple", "none"])
def test_route_after_composite_text_lock_off_generates(level):
    assert pipeline_mod._route_after_composite({"mode": "generate", "text_level": level}) == "generate"


# ── 그래프 끝까지 ──
def _spy_read_text_node(monkeypatch):
    """read_text 노드 실행 여부 — build() 가 호출 시점의 모듈 함수를 묶으므로 새로 빌드."""
    ran = []
    real = pipeline_mod.read_text
    monkeypatch.setattr(pipeline_mod, "read_text", lambda s: ran.append(1) or real(s))
    monkeypatch.setattr(pipeline_mod, "GRAPH", pipeline_mod.build())
    return ran


def _poison_item_text(monkeypatch):
    def poison(*a, **k):
        raise AssertionError("read_item_text 호출 금지")
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text", poison)


def _nonstrict(reads):
    return [r for r in reads if not r.get("strict")]


@pytest.mark.parametrize("lock,level", [(False, "simple"), (False, None), (True, "none")])
def test_graph_skips_read_text_node(monkeypatch, make_png, lock, level):
    """text_lock 꺼짐(어떤 수준이든) 또는 글자 없음(none) → read_text 노드 없이 바로 생성."""
    monkeypatch.setattr(settings, "text_lock", lock)
    calls = _patch_graph_deps(monkeypatch, make_png, [[]], text_level=level)
    ran = _spy_read_text_node(monkeypatch)
    _poison_item_text(monkeypatch)

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert ran == [] and calls["gen_seeds"] == [None]
    assert out["mode"] == "generate" and out["gate_passed"] is True
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["item_texts"] == [] and ins["text_level"] == level


def test_graph_simple_runs_read_text_once(monkeypatch, make_png):
    monkeypatch.setattr(settings, "text_lock", True)
    calls = _patch_graph_deps(monkeypatch, make_png, [[]], text_level="simple")
    ran = _spy_read_text_node(monkeypatch)
    reads = _text_reader(monkeypatch, [{"text": "SALE"}], [{"text": "SALE"}])

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert ran == [1] and len(_nonstrict(reads)) == 1
    assert calls["gen_seeds"] == [None] and out["status"] == "pass"


def _compose_spy(monkeypatch, make_png, calls):
    boxes = []

    def compose(original, bg, box=None):
        calls["compose"] += 1
        boxes.append(box)
        return make_png(color=COMP_COLOR)
    monkeypatch.setattr(pipeline_mod.compositor, "compose", compose)
    return boxes


@pytest.mark.parametrize("detect_box,expected", [(_B_DETECT, _B_DETECT), (None, _B_READ)])
def test_graph_read_text_item_box_priority_reaches_compose(monkeypatch, make_png,
                                                           detect_box, expected):
    """text_heavy 로 배경 교체: 오리기 범위는 detect 박스 우선, 없으면 read_text 박스."""
    monkeypatch.setattr(settings, "text_lock", True)
    calls = _patch_graph_deps(monkeypatch, make_png, [], item_box=detect_box)
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text",
                        lambda img, item, **kw: {"item_box": _B_READ, "texts": _texts(12)})
    boxes = _compose_spy(monkeypatch, make_png, calls)

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert out["composite_reason"] == "text_heavy" and calls["gen_seeds"] == []
    assert boxes == [expected]


def test_graph_dense_goes_to_composite_without_read_text_or_generate(monkeypatch, make_png):
    monkeypatch.setattr(settings, "text_lock", True)
    calls = _patch_graph_deps(monkeypatch, make_png, [], text_level="dense", item_box=_B_DETECT)
    ran = _spy_read_text_node(monkeypatch)
    _poison_item_text(monkeypatch)
    monkeypatch.setattr(pipeline_mod, "_generate_ai",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("생성 금지")))
    boxes = _compose_spy(monkeypatch, make_png, calls)
    seen = []
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate",
                        lambda img, t, i, c, **kw: seen.append(t) or
                        [{"what": x["what"], "preserved": True} for x in t])

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert ran == []
    assert out["mode"] == "composite" and out["composite_reason"] == "text_dense"
    assert out["gate_passed"] is True and out["status"] == "pass"
    assert boxes == [_B_DETECT]                        # detect 박스로 오림
    assert [[t["what"] for t in ts] for ts in seen] == [["얼룩"]]   # 앵커만 확인
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["text_level"] == "dense" and ins["composite_reason"] == "text_dense"
    assert ins["item_texts"] == []


def test_graph_dense_composite_failure_reads_text_once_then_generates_with_lock(
        monkeypatch, make_png):
    """dense → 오리기 실패 → read_text(글자 12줄이어도 text_heavy 로 되돌아가지 않음)
    → TEXT_LOCK 붙여 생성 1회. 무한 왕복 없음."""
    monkeypatch.setattr(settings, "text_lock", True)
    calls = _patch_graph_deps(monkeypatch, make_png, [[]], text_level="dense")
    _compose_fails(monkeypatch, calls)
    comp_runs = []
    real_composite = pipeline_mod.composite
    monkeypatch.setattr(pipeline_mod, "composite",
                        lambda s: comp_runs.append(s.get("composite_reason")) or real_composite(s))
    ran = _spy_read_text_node(monkeypatch)
    reads = _text_reader(monkeypatch, _texts(12), _texts(12))
    prompts = []
    monkeypatch.setattr(pipeline_mod, "_generate_ai",
                        lambda original, preset, seed=None: prompts.append(preset["prompt"])
                        or make_png(color=GEN_COLOR))

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"},
                                    {"recursion_limit": pipeline_mod.RECURSION_LIMIT})

    assert calls["compose"] == 1 and ran == [1]
    # composite 노드는 한 번뿐 — read_text 가 text_heavy 로 다시 보내지 않는다
    assert comp_runs == ["text_dense"]
    assert len(_nonstrict(reads)) == 1                 # 원본 글자 읽기 딱 1회
    assert len(prompts) == 1 and '"t00"' in prompts[0]  # TEXT_LOCK 적용
    assert out["mode"] == "generate" and out["composite_reason"] is None
    assert out["composite_error"] == "물건을 찾지 못함"
    assert out["text_level"] == "dense" and len(out["item_texts"]) == 12
    assert out["status"] == "pass" and out["gate_passed"] is True
    assert len(calls["ocr"][0][0]) == pipeline_mod.TEXT_VERIFY_MAX   # OCR 가드도 켜짐


def test_graph_none_composite_failure_generates_directly(monkeypatch, make_png):
    monkeypatch.setattr(settings, "text_lock", True)
    monkeypatch.setattr(settings, "composite_first_min_anchors", 1)   # plan → many_defects
    calls = _patch_graph_deps(monkeypatch, make_png, [[]], text_level="none")
    _compose_fails(monkeypatch, calls)
    ran = _spy_read_text_node(monkeypatch)
    _poison_item_text(monkeypatch)

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"},
                                    {"recursion_limit": pipeline_mod.RECURSION_LIMIT})

    assert ran == [] and calls["compose"] == 1 and calls["gen_seeds"] == [None]
    assert out["mode"] == "generate" and out["gate_passed"] is True
    assert "item_texts" not in out


def test_graph_simple_many_defects_composite_failure_reads_text(monkeypatch, make_png):
    """simple 인데 many_defects 로 먼저 배경 교체 → 실패 → 글자 아직 안 읽음 → read_text."""
    monkeypatch.setattr(settings, "text_lock", True)
    monkeypatch.setattr(settings, "composite_first_min_anchors", 1)
    calls = _patch_graph_deps(monkeypatch, make_png, [[]], text_level="simple")
    _compose_fails(monkeypatch, calls)
    ran = _spy_read_text_node(monkeypatch)
    reads = _text_reader(monkeypatch, [{"text": "SALE"}], [{"text": "SALE"}])

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"},
                                    {"recursion_limit": pipeline_mod.RECURSION_LIMIT})

    assert ran == [1] and len(_nonstrict(reads)) == 1
    assert calls["compose"] == 1 and calls["gen_seeds"] == [None]
    assert out["mode"] == "generate" and out["item_texts"] == [{"text": "SALE"}]


def test_graph_detect_failed_composite_failure_reads_text(monkeypatch, make_png):
    """detect 실패(text_level None) → 오리기 실패 → read_text → 생성 (글자 보호는 켠다).
    detect 실패라 verify 는 gate False → 배경 교체 재시도 없이 composite_failed."""
    monkeypatch.setattr(settings, "text_lock", True)
    calls = _patch_graph_deps(monkeypatch, make_png, [[]])
    _detect_seq(monkeypatch, [ValueError("x")] * 2)
    _no_vlm_verify(monkeypatch)
    _compose_fails(monkeypatch, calls)
    ran = _spy_read_text_node(monkeypatch)
    reads = _text_reader(monkeypatch, [{"text": "SALE"}], [{"text": "SALE"}])
    prompts = []
    monkeypatch.setattr(pipeline_mod, "_generate_ai",
                        lambda original, preset, seed=None: prompts.append(preset["prompt"])
                        or make_png(color=GEN_COLOR))

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert ran == [1] and len(_nonstrict(reads)) == 1
    assert len(prompts) == 1 and '"SALE"' in prompts[0]
    assert calls["compose"] == 1
    assert out["detect_failed"] is True and out["mode"] == "composite_failed"
    assert out["gate_passed"] is False
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["text_level"] is None and ins["item_texts"] == [{"text": "SALE"}]


def test_graph_dense_composite_failure_read_failure_still_generates(monkeypatch, make_png):
    monkeypatch.setattr(settings, "text_lock", True)
    calls = _patch_graph_deps(monkeypatch, make_png, [[]], text_level="dense")
    _compose_fails(monkeypatch, calls)
    reads = _text_reader(monkeypatch, RuntimeError("vlm down"), [])

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"},
                                    {"recursion_limit": pipeline_mod.RECURSION_LIMIT})

    assert len(reads) == 1 and calls["gen_seeds"] == [None]
    assert out["mode"] == "generate" and out["item_texts"] == []


def test_worst_path_dense_composite_failure_with_text_lock_fits_recursion_limit(
        monkeypatch, make_png):
    """dense → 합성 실패 → read_text → 생성 한도 5 + 가드 재시도 + 게이트 재생성 + 합성 실패."""
    from dataclasses import dataclass
    monkeypatch.setattr(settings, "text_lock", True)
    monkeypatch.setattr(settings, "max_generate_attempts", 5)
    calls = _patch_graph_deps(monkeypatch, make_png, [], text_level="dense")
    _compose_fails(monkeypatch, calls)
    _text_reader(monkeypatch, _texts(12), _texts(12))

    @dataclass
    class G:
        name: str = "x"; passed: bool = False; value: float = 0; threshold: float = 1; severity: str = "hard"
    state = {"n": 0}

    def first_fails(orig, result, a, b):
        state["n"] += 1
        return [G()] if state["n"] == 1 else []
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", first_fails)
    monkeypatch.setattr(pipeline_mod.guards, "decide",
                        lambda results: ("block" if any(not g.passed for g in results) else "pass", None))
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": False, "reason": "cropped"})
    _verify_seq(monkeypatch, [False] * 5)

    out = pipeline_mod.run_transform("fid-g", "studio_white")
    assert out["mode"] == "composite_failed" and calls["compose"] == 1


# ── dev 그래프 ──
def _dev_deps(monkeypatch, make_png, **kw):
    calls = _patch_graph_deps(monkeypatch, make_png, [], **kw)

    def poison(*a, **k):
        raise AssertionError("dev 그래프에서 생성·합성 금지")
    monkeypatch.setattr(pipeline_mod, "_generate_ai", poison)
    monkeypatch.setattr(pipeline_mod.compositor, "compose", poison)
    return calls


def test_dev_graph_text_heavy_does_not_composite(monkeypatch, make_png):
    monkeypatch.setattr(settings, "text_lock", True)
    _dev_deps(monkeypatch, make_png, text_level="simple")
    reads = _text_reader(monkeypatch, _texts(12), [])

    out = pipeline_mod.run_transform_with_result("fid-g", "studio_white",
                                                 make_png(color=GEN_COLOR))

    assert len(reads) == 1
    assert out["mode"] == "generate" and out["prompt_used"].startswith("TEST:")
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["composite_reason"] is None and len(ins["item_texts"]) == 12


def test_dev_graph_dense_skips_read_text_and_uses_provided(monkeypatch, make_png):
    monkeypatch.setattr(settings, "text_lock", True)
    _dev_deps(monkeypatch, make_png, text_level="dense")
    _poison_item_text(monkeypatch)

    out = pipeline_mod.run_transform_with_result("fid-g", "studio_white",
                                                 make_png(color=GEN_COLOR))

    assert out["mode"] == "generate" and out["gate_passed"] is True
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["text_level"] == "dense" and ins["composite_reason"] is None
    assert ins["item_texts"] == []


# ── save_inspect ──
@pytest.mark.parametrize("level", ["none", "simple", "dense", None])
def test_save_inspect_writes_text_level(level):
    pipeline_mod.save_inspect({"file_id": "si", "preset_key": "p", "anchors": [],
                               "text_level": level})
    ins = json.loads(storage.load("quality", "si_p_inspect.json"))
    assert "text_level" in ins and ins["text_level"] == level


def test_save_inspect_text_level_missing_is_none():
    pipeline_mod.save_inspect({"file_id": "si", "preset_key": "p", "anchors": []})
    ins = json.loads(storage.load("quality", "si_p_inspect.json"))
    assert "text_level" in ins and ins["text_level"] is None
