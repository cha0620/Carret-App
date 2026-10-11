"""정직함 라벨 도구 (10-11) — 저장 · 집계. 이미지 · 서버 없이."""
import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location("label_honesty", Path(__file__).parents[4] / "eval" / "label_honesty.py")
L = importlib.util.module_from_spec(spec)
spec.loader.exec_module(L)


def test_save_label_keeps_only_known_flags_and_summary_counts(tmp_path, monkeypatch):
    monkeypatch.setattr(L, "LABELS", tmp_path / "labels.json")
    L.save_label("r/a", {"same": True, "evil": True, "memo": "x" * 900})
    L.save_label("r/b", {"changed": True, "added": True})
    L.save_label("r/c", {"unsure": True})
    got = L.load_labels()
    assert "evil" not in got["r/a"] and len(got["r/a"]["memo"]) == 500
    cases = [{"id": i} for i in ("r/a", "r/b", "r/c", "r/d")]
    assert L.summary(cases, got) == {"cases": 4, "labeled": 3, "unsure": 1, "same": 1, "differ": 1,
                                     "added": 1, "missing": 0, "changed": 1}
