"""app.services.pipeline - `run_transform`/`run_transform_with_result` 트레이싱
투명성.

두 함수 모두 무거운 LangGraph/모델 호출(VLM/fal.ai/DINOv2)을 감싸므로,
그래프 실행 자체(`pipeline.GRAPH.invoke`) 나 개별 노드 함수를 가짜로
바꿔치기해서 실제 네트워크/모델 다운로드 없이 "트레이싱 on/off 와 무관하게
반환값이 동일한가" 만 검증한다(비즈니스 로직 변경 없음 확인 목적 - 그래프
조립/노드 로직은 안 건드림). `score_similarity` 는 실제 DINOv2 모델을 로드하므로
반드시 mock 한다 - 안 그러면 huggingface 모델 다운로드로 네트워크가 튄다.
"""
import pytest

import app.core.tracing as tracing
import app.services.pipeline as pipeline_mod
from app.core.config import settings


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


@pytest.fixture(autouse=True)
def restore_pipeline_mode(monkeypatch):
    monkeypatch.setattr(settings, "pipeline_mode", "real", raising=False)
    yield


class FakeObservation:
    def __init__(self):
        self.update_calls = []
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
    def __init__(self):
        self.calls = []
        self.obs = FakeObservation()
        self.flushed = False
    def start_as_current_observation(self, **kw):
        self.calls.append(kw)
        return FakeObservationCM(self.obs)
    def flush(self):
        self.flushed = True


class FakeGraph:
    """`pipeline.GRAPH.invoke(state)` 흉내 - 실제 그래프/모델 호출 없음."""
    def __init__(self, out):
        self._out = out
        self.invoked_with = None
    def invoke(self, state):
        self.invoked_with = state
        return self._out


def _graph_out():
    return {
        "result_name": "abc_preset.jpg",
        "prompt_used": "some prompt",
        "checks": [{"what": "얼룩", "preserved": True}],
        "bubbles": [{"what": "얼룩", "label": "얼룩"}],
        "gate_passed": True,
        "item": "chair",
        "considered": ["scratch"],
        "visual_similarity": 0.87,
    }


def _expected(out):
    return {
        "result_name": out["result_name"],
        "prompt_used": out["prompt_used"],
        "checks": out["checks"],
        "bubbles": out["bubbles"],
        "gate_passed": out["gate_passed"],
        "item": out["item"],
        "considered": out["considered"],
        "visual_similarity": out["visual_similarity"],
    }


# ── run_transform(): mock 파이프라인 모드 (지름길, 트레이싱 없음) ──
def test_run_transform_mock_mode_bypasses_graph_and_tracing(monkeypatch, make_png):
    monkeypatch.setattr(settings, "pipeline_mode", "mock", raising=False)

    def _poison_invoke(state):
        raise AssertionError("mock 모드는 그래프를 타면 안 된다")
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(None), raising=False)
    monkeypatch.setattr(pipeline_mod.GRAPH, "invoke", _poison_invoke)

    from app.services import storage
    storage.save("original", "fid1.jpg", make_png())

    result = pipeline_mod.run_transform("fid1", "preset_a")

    assert result == {
        "result_name": "fid1_preset_a.jpg", "prompt_used": "PASS-THROUGH",
        "checks": [], "bubbles": [], "gate_passed": None,
        "item": "object", "considered": [],
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
    assert fake_graph.invoked_with == {"file_id": "fid2", "preset_key": "preset_b"}


def test_run_transform_real_mode_disabled_tracing_missing_visual_similarity_defaults_none(monkeypatch):
    """out 에 visual_similarity 키가 아예 없어도(과거 그래프 산출물 등) .get() 이
    안전하게 None 으로 떨어져야 한다."""
    out = _graph_out()
    del out["visual_similarity"]
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(out), raising=False)

    result = pipeline_mod.run_transform("fid2b", "preset_b")

    assert result["visual_similarity"] is None


# ── run_transform(): real 모드, 트레이싱 활성 ──
def test_run_transform_real_mode_enabled_tracing_updates_obs_and_flushes(monkeypatch):
    fake_lf = FakeLangfuseClient()
    monkeypatch.setattr(tracing, "_disabled", False, raising=False)
    monkeypatch.setattr(tracing, "_client", fake_lf, raising=False)

    out = _graph_out()
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(out), raising=False)

    result = pipeline_mod.run_transform("fid3", "preset_c")

    assert result == _expected(out)
    assert fake_lf.calls[0]["name"] == "transform"
    assert fake_lf.calls[0]["as_type"] == "span"
    assert fake_lf.obs.update_calls == [{"output": {
        "gate_passed": True, "bubbles": 1, "item": "chair",
        "visual_similarity": 0.87,
    }}]
    assert fake_lf.flushed is True


# ── run_transform_with_result(): dev 경로, 노드 직접 호출 ──
def _patch_nodes(monkeypatch, checks, gate_passed, bubbles_out, visual_similarity=0.5):
    monkeypatch.setattr(pipeline_mod, "load", lambda s: {
        "original": b"ORIGINAL", "preset": {"prompt": "P", "name": "n", "bg_color": "#fff"},
    })
    monkeypatch.setattr(pipeline_mod, "classify_node", lambda s: {
        "item": "chair", "considered": ["scratch"],
    })
    monkeypatch.setattr(pipeline_mod, "detect", lambda s: {
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
    }
    from app.services import storage
    assert storage.load("result", "fid4_preset_d.jpg") is not None


def test_run_transform_with_result_enabled_tracing_updates_obs_and_flushes(monkeypatch, make_png):
    fake_lf = FakeLangfuseClient()
    monkeypatch.setattr(tracing, "_disabled", False, raising=False)
    monkeypatch.setattr(tracing, "_client", fake_lf, raising=False)

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
