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
        "gen_attempts": 1,          # ⭐ 재생성 게이트
        "photo_check": {"valid": True, "reason": ""},   # ⭐ 재생성 게이트
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
        "gen_attempts": out.get("gen_attempts"),
        "photo_check": out.get("photo_check"),
        "status": out.get("status", "pass"),
        "guard_report": out.get("guard_report", []),
        "mode": out.get("mode", "generate"),
    }


# ── run_transform(): mock 파이프라인 모드 (지름길, 트레이싱 없음) ──
def test_run_transform_mock_mode_bypasses_graph_and_tracing(monkeypatch, make_png):
    monkeypatch.setattr(settings, "pipeline_mode", "mock", raising=False)

    def _poison_invoke(state):
        raise AssertionError("mock 모드는 그래프를 타면 안 된다")
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph(None), raising=False)
    monkeypatch.setattr(pipeline_mod.GRAPH, "invoke", _poison_invoke)

    from app.services.persistence import storage
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
        "gen_attempts": 1, "photo_check": {"valid": True, "reason": ""},
        "mode": "generate",
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
    from app.services.persistence import storage
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


# ── 출력 가드 폴백 정책 (validate_result 안에서 실행, 그래프 구조 불변) ──
from app.services.quality.guards import GuardResult, run_output_guards as _ORIG_RUN_OUTPUT_GUARDS

ORIG_COLOR = (120, 90, 60)   # make_png 기본색
GEN_COLOR = (250, 250, 250)


def _hard_fail():
    return GuardResult(name="ocr_match", passed=False, value=0.5,
                       threshold=0.95, severity="hard")


def _soft_fail():
    return GuardResult(name="dino_band", passed=False, value=0.6,
                       threshold=0.75, severity="soft")


def _patch_graph_deps(monkeypatch, make_png, guard_side_effects):
    """guard_side_effects: generate 1회당 run_output_guards 가 돌려줄 리스트를
    순서대로. 반환: 호출 기록 dict (gen_seeds / judge / verify)."""
    from app.services.persistence import storage
    storage.save("original", "fid-g.png", make_png())

    calls = {"gen_seeds": [], "judge": 0, "verify": 0}

    def fake_generate_ai(original, preset, seed=None):
        calls["gen_seeds"].append(seed)
        return make_png(color=GEN_COLOR)

    guard_iter = iter(guard_side_effects)

    def fake_guards(orig, result, anchors, ocr_before, ocr_after):
        assert ocr_before == [] and ocr_after == []   # T3 전까지 OCR 미연결
        return next(guard_iter)

    def fake_judge(orig, result):
        calls["judge"] += 1
        return {"analysis": "ok", "fidelity": 5, "realism": 5, "trust": 5}

    def fake_verify(img, anchors, item, considered):
        calls["verify"] += 1
        return [{"what": "얼룩", "preserved": True}]

    monkeypatch.setattr(pipeline_mod.detector, "classify",
                        lambda img: {"item": "chair", "considered": []})
    monkeypatch.setattr(pipeline_mod.detector, "detect_defects",
                        lambda img, item, considered: [
                            {"category": "other", "what": "얼룩", "where": "앞면"}])
    monkeypatch.setattr(pipeline_mod, "_generate_ai", fake_generate_ai)
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", fake_guards)
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": True, "reason": ""})
    monkeypatch.setattr(pipeline_mod.embedder, "cosine_similarity",
                        lambda orig, result: 0.9)
    monkeypatch.setattr(pipeline_mod.detector, "verify_and_locate", fake_verify)
    monkeypatch.setattr(pipeline_mod.judge, "judge", fake_judge)
    monkeypatch.setattr(pipeline_mod.compositor, "compose",
                        lambda original, bg, box=None: make_png(color=(9, 9, 9)))
    return calls


def _mean_color(image_bytes):
    import io
    from PIL import Image
    img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    return img.resize((1, 1)).getpixel((0, 0))


def test_guard_hard_fail_twice_returns_blocked_with_original(monkeypatch, make_png):
    calls = _patch_graph_deps(monkeypatch, make_png,
                              [[_hard_fail()], [_hard_fail(), _soft_fail()]])

    result = pipeline_mod.run_transform("fid-g", "studio_white")

    assert result["status"] == "blocked"
    assert [g["name"] for g in result["guard_report"]] == ["ocr_match", "dino_band"]
    assert result["guard_report"][0] == {
        "name": "ocr_match", "passed": False, "value": 0.5,
        "threshold": 0.95, "severity": "hard"}
    # 변환 이미지는 내보내지 않는다 — 결과 슬롯엔 원본이 들어가 있어야 함
    from app.services.persistence import storage
    served = storage.load("result", result["result_name"])
    assert all(abs(a - b) <= 2 for a, b in zip(_mean_color(served), ORIG_COLOR))
    # blocked 결과로 verify/judge(비용 드는 VLM)를 돌리지 않는다
    assert calls["verify"] == 0 and calls["judge"] == 0
    assert storage.load("quality", "fid-g_studio_white.json") is None


def test_guard_seed_retry_happens_exactly_once(monkeypatch, make_png):
    """항상 hard fail 이어도 generate 는 최초 1회 + seed 재시도 1회 = 2회뿐."""
    calls = _patch_graph_deps(monkeypatch, make_png,
                              [[_hard_fail()], [_hard_fail()], [_hard_fail()]])

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert len(calls["gen_seeds"]) == 2
    assert calls["gen_seeds"][0] is None            # 최초: seed 미지정 (fal 기본)
    assert isinstance(calls["gen_seeds"][1], int)   # 재시도: seed 만 바뀜
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
    assert calls["gen_seeds"] == [None]     # soft 는 재시도 사유가 아님
    assert calls["judge"] == 1


def test_guard_report_written_to_inspect_json(monkeypatch, make_png):
    import json
    _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], [_hard_fail()]])

    pipeline_mod.run_transform("fid-g", "studio_white")

    from app.services.persistence import storage
    inspect = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert inspect["status"] == "blocked"
    assert inspect["guard_report"][0]["name"] == "ocr_match"
    assert isinstance(inspect["guard_seed"], int)


def test_guard_exception_propagates(monkeypatch, make_png):
    """가드 계산 실패는 fail-open 하지 않는다 — 예외 그대로 전파."""
    _patch_graph_deps(monkeypatch, make_png, [])

    def boom(*a, **kw):
        raise RuntimeError("embedder down")
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", boom)

    with pytest.raises(RuntimeError, match="embedder down"):
        pipeline_mod.run_transform("fid-g", "studio_white")


def test_guard_retry_raising_leaves_no_generated_image(monkeypatch, make_png):
    """가드 재시도 호출이 예외로 끝나도 가드 전 이미지가 결과 슬롯에 남으면 안 된다."""
    _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()]])

    def gen(original, preset, seed=None):
        if seed is not None:
            raise TimeoutError("fal timeout")
        return make_png(color=GEN_COLOR)
    monkeypatch.setattr(pipeline_mod, "_generate_ai", gen)

    with pytest.raises(TimeoutError):
        pipeline_mod.run_transform("fid-g", "studio_white")

    from app.services.persistence import storage
    assert storage.load("result", "fid-g_studio_white.jpg") is None


def test_guard_retry_does_not_consume_photo_budget(monkeypatch, make_png):
    """가드 재시도 1회는 max_generate_attempts 와 별개 — 가드 재시도 뒤에도
    photo_check 재생성 기회가 남아 있고, 그 재생성엔 seed 를 고정하지 않는다."""
    monkeypatch.setattr(settings, "max_generate_attempts", 2, raising=False)
    calls = _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], [], []])
    checks = iter([{"valid": False, "reason": "cropped"}, {"valid": True, "reason": ""}])
    monkeypatch.setattr(pipeline_mod.detector, "check_photo", lambda img: next(checks))

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    seeds = calls["gen_seeds"]
    assert len(seeds) == 3                       # 최초 + 가드 재시도 + photo 재생성
    assert seeds[0] is None and isinstance(seeds[1], int) and seeds[2] is None
    assert out["gen_attempts"] == 2
    assert out["status"] == "pass"


def test_hard_fail_after_guard_retry_used_blocks_immediately(monkeypatch, make_png):
    """가드 재시도를 이미 쓴 뒤 photo 재생성에서 hard fail → 추가 재시도 없이 blocked."""
    monkeypatch.setattr(settings, "max_generate_attempts", 2, raising=False)
    calls = _patch_graph_deps(monkeypatch, make_png, [[_hard_fail()], [], [_hard_fail()]])
    monkeypatch.setattr(pipeline_mod.detector, "check_photo",
                        lambda img: {"valid": False, "reason": "cropped"})

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert len(calls["gen_seeds"]) == 3
    assert out["status"] == "blocked"
    assert out["photo_check"] is None


def test_guard_anchors_only_boxed_passed(monkeypatch, make_png):
    seen = {}
    _patch_graph_deps(monkeypatch, make_png, [])
    boxed = {"type": "defect", "box": {"x1": 0, "y1": 0, "x2": 10, "y2": 10}}
    monkeypatch.setattr(pipeline_mod.detector, "detect_defects",
                        lambda img, item, considered: [
                            {"category": "other", "what": "얼룩", "where": "앞면"}, boxed])

    def spy(orig, result, anchors, ocr_before, ocr_after):
        seen["anchors"] = anchors
        return []
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", spy)

    pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert seen["anchors"] == [boxed]


def test_real_guards_with_empty_ocr_pass(monkeypatch, make_png):
    """실제 run_output_guards 에 OCR [] 를 넣어도 hard fail 이 안 나야 한다
    (text_recall("", "") 이 1.0 이 아니게 바뀌면 모든 실행이 blocked 됨)."""
    calls = _patch_graph_deps(monkeypatch, make_png, [])
    monkeypatch.setattr(pipeline_mod.guards, "run_output_guards", _ORIG_RUN_OUTPUT_GUARDS)

    out = pipeline_mod.GRAPH.invoke({"file_id": "fid-g", "preset_key": "studio_white"})

    assert out["status"] == "pass"
    assert calls["gen_seeds"] == [None]


def test_text_lock_puts_original_text_into_generate_prompt(monkeypatch, make_png):
    """원본 물건 위 글자를 읽어 generate 프롬프트에 넣고, 인스펙트에도 남긴다."""
    import json
    from app.core.config import settings
    from app.services.persistence import storage
    _patch_graph_deps(monkeypatch, make_png, [[]])
    monkeypatch.setattr(settings, "text_lock", True)
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text", lambda img, item: {
        "item_box": None,
        "texts": [{"text": "시한부", "x1": 300, "y1": 100, "x2": 700, "y2": 200}]})
    prompts = []
    monkeypatch.setattr(pipeline_mod, "_generate_ai",
                        lambda original, preset, seed=None: prompts.append(preset["prompt"]) or make_png())

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert '"시한부" (top-center)' in prompts[0]
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["item_texts"][0]["text"] == "시한부"
    assert out["status"] == "pass"


def test_text_lock_read_failure_generates_without_text(monkeypatch, make_png):
    from app.core.config import settings
    _patch_graph_deps(monkeypatch, make_png, [[]])
    monkeypatch.setattr(settings, "text_lock", True)

    def boom(img, item):
        raise RuntimeError("vlm down")
    monkeypatch.setattr(pipeline_mod.detector, "read_item_text", boom)
    prompts = []
    monkeypatch.setattr(pipeline_mod, "_generate_ai",
                        lambda original, preset, seed=None: prompts.append(preset["prompt"]) or make_png())

    pipeline_mod.run_transform("fid-g", "studio_white")
    assert "Text printed on the product" not in prompts[0]



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
    import json
    from app.services.persistence import storage
    _patch_graph_deps(monkeypatch, make_png, [[], []])
    verify_calls = _verify_seq(monkeypatch, [False, False, True])
    composed = make_png(color=(9, 9, 9))
    monkeypatch.setattr(pipeline_mod.compositor, "compose", lambda original, bg, box=None: composed)

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert out["mode"] == "composite" and out["gate_passed"] is True
    assert len(verify_calls) == 3                     # 합성본을 다시 verify
    saved = storage.load("result", "fid-g_studio_white.jpg")
    assert _mean_color(saved)[0] < 30                   # 저장된 결과 = 합성본 (거의 검정)
    assert _mean_color(verify_calls[-1])[0] < 30
    ins = json.loads(storage.load("quality", "fid-g_studio_white_inspect.json"))
    assert ins["mode"] == "composite" and ins["gate_retried"] is True


def test_composite_failure_keeps_generated_result_and_stops(monkeypatch, make_png):
    _patch_graph_deps(monkeypatch, make_png, [[], []])
    verify_calls = _verify_seq(monkeypatch, [False, False])

    def boom(original, bg, box=None):
        raise ValueError("물건을 찾지 못함")
    monkeypatch.setattr(pipeline_mod.compositor, "compose", boom)

    out = pipeline_mod.run_transform("fid-g", "studio_white")

    assert out["mode"] == "composite_failed" and out["gate_passed"] is False
    assert len(verify_calls) == 2       # 무한 루프 없이 끝남
