"""pipeline.run_transform()/run_transform_with_result() 이 실제로
`results` 테이블(store.record_result)에 row 를 남기는지 — 이전엔 만들어져
있었지만 아무도 호출하지 않던 store.record_result 가 파이프라인 끝에 실제로
연결됐는지 검증하는 엔드투엔드(단, VLM/fal.ai/DINOv2 는 여전히 mock) 테스트.

DB/storage 격리는 conftest.py 의 autouse fixture(isolated_db/isolated_storage)가
매 테스트마다 처리해준다."""
import pytest

import app.core.tracing as tracing
import app.services.pipeline as pipeline_mod
from app.core.config import settings
from app.services.persistence import store


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    """.env 에 실제 Langfuse 키가 있어도 테스트에서는 절대 네트워크로 나가지 않도록."""
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)


class FakeGraph:
    """`pipeline.GRAPH.invoke(state)` 흉내 - 실제 그래프/모델 호출 없음."""
    def __init__(self, outputs):
        self._outputs = list(outputs)

    def invoke(self, state):
        return self._outputs.pop(0)


def _out(**overrides):
    base = {
        "result_name": "abc_preset.jpg",
        "prompt_used": "some prompt",
        "checks": [{"what": "얼룩", "preserved": True}],
        "bubbles": [{"what": "얼룩", "label": "얼룩"}],
        "gate_passed": True,
        "item": "chair",
        "considered": ["scratch"],
        "visual_similarity": 0.87,
    }
    base.update(overrides)
    return base


# ── mock 파이프라인 모드 (지름길) ──────────────────────────────
def test_run_transform_mock_mode_writes_results_row(monkeypatch, make_png):
    monkeypatch.setattr(settings, "pipeline_mode", "mock", raising=False)

    from app.services.persistence import storage
    storage.save("original", "fid-mock.jpg", make_png())

    assert store.get_result("fid-mock", "preset_a") is None

    pipeline_mod.run_transform("fid-mock", "preset_a")

    row = store.get_result("fid-mock", "preset_a")
    assert row is not None
    assert row["file_id"] == "fid-mock"
    assert row["preset_key"] == "preset_a"
    assert row["result_name"] == "fid-mock_preset_a.jpg"
    assert row["item"] == "object"
    assert row["gate_passed"] is None
    assert row["elapsed_s"] is not None


def test_run_transform_mock_mode_second_call_updates_not_duplicates(monkeypatch, make_png):
    monkeypatch.setattr(settings, "pipeline_mode", "mock", raising=False)

    from app.services.persistence import storage
    storage.save("original", "fid-mock2.jpg", make_png())

    pipeline_mod.run_transform("fid-mock2", "preset_a")
    first = store.get_result("fid-mock2", "preset_a")

    pipeline_mod.run_transform("fid-mock2", "preset_a")
    second = store.get_result("fid-mock2", "preset_a")

    assert second["id"] == first["id"]  # 같은 row (upsert), 신규 삽입 아님

    from app.core import db
    with db.get_conn() as c:
        count = c.execute(
            "SELECT COUNT(*) AS n FROM results WHERE file_id=? AND preset_key=?",
            ("fid-mock2", "preset_a"),
        ).fetchone()["n"]
    assert count == 1


# ── real 모드 (그래프는 fake, DB 연결만 검증) ──────────────────
def test_run_transform_real_mode_writes_results_row(monkeypatch):
    out = _out()
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph([out]), raising=False)

    pipeline_mod.run_transform("fid-real", "preset_b")

    row = store.get_result("fid-real", "preset_b")
    assert row is not None
    assert row["result_name"] == "abc_preset.jpg"
    assert row["item"] == "chair"
    assert row["gate_passed"] == 1
    import json
    assert json.loads(row["considered"]) == ["scratch"]
    assert json.loads(row["bubbles"]) == [{"what": "얼룩", "label": "얼룩"}]


def test_run_transform_real_mode_second_call_with_changed_fields_updates_row(monkeypatch):
    """같은 (file_id, preset_key) 에 대해 두 번째 호출의 산출물이 바뀌었으면
    row가 새로 갱신되어야 한다 (신규 row 추가가 아니라 UPDATE)."""
    first_out = _out(gate_passed=True, bubbles=[{"what": "얼룩", "label": "얼룩"}],
                     result_name="v1.jpg")
    second_out = _out(gate_passed=False, bubbles=[], result_name="v2.jpg")
    monkeypatch.setattr(pipeline_mod, "GRAPH", FakeGraph([first_out, second_out]), raising=False)

    pipeline_mod.run_transform("fid-real2", "preset_c")
    first_row = store.get_result("fid-real2", "preset_c")

    pipeline_mod.run_transform("fid-real2", "preset_c")
    second_row = store.get_result("fid-real2", "preset_c")

    assert second_row["id"] == first_row["id"]
    assert second_row["result_name"] == "v2.jpg"
    assert second_row["gate_passed"] == 0
    import json
    assert json.loads(second_row["bubbles"]) == []

    from app.core import db
    with db.get_conn() as c:
        count = c.execute(
            "SELECT COUNT(*) AS n FROM results WHERE file_id=? AND preset_key=?",
            ("fid-real2", "preset_c"),
        ).fetchone()["n"]
    assert count == 1


# ── run_transform_with_result(): dev 경로 ──────────────────────
def test_run_transform_with_result_writes_results_row(monkeypatch, make_png):
    monkeypatch.setattr(pipeline_mod, "load", lambda s: {
        "original": b"ORIGINAL", "preset": {"prompt": "P", "name": "n", "bg_color": "#fff"},
    })
    monkeypatch.setattr(pipeline_mod, "classify_node", lambda s: {
        "item": "chair", "considered": ["scratch"],
    })
    monkeypatch.setattr(pipeline_mod, "detect", lambda s: {"anchors": []})
    monkeypatch.setattr(pipeline_mod, "score_similarity", lambda s: {"visual_similarity": 0.5})
    monkeypatch.setattr(pipeline_mod, "verify", lambda s: {
        "checks": [{"what": "얼룩", "preserved": True}], "gate_passed": True,
    })
    monkeypatch.setattr(pipeline_mod, "save_inspect", lambda s: {})
    monkeypatch.setattr(pipeline_mod, "run_judge", lambda s: {})
    monkeypatch.setattr(pipeline_mod, "finalize", lambda s: {"bubbles": []})

    pipeline_mod.run_transform_with_result("fid-dev", "preset_d", make_png())

    row = store.get_result("fid-dev", "preset_d")
    assert row is not None
    assert row["result_name"] == "fid-dev_preset_d.jpg"
    assert row["item"] == "chair"
    assert row["gate_passed"] == 1
