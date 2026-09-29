"""eval/run.py — _write_review · _isolate · _confirm (파이프라인·VLM 호출 없음)."""
import csv
import os

import pytest


def _rows():
    return [
        {"file": "bag.webp", "repeat": 1, "mode": "generate", "photo_type": "product", "wear_level": "light",
         "orig": "files/bag__r1_orig.webp", "result": "files/bag__r1_result.jpg", "composite_reason": None},
        {"file": "boom.webp", "repeat": 1, "error": "RuntimeError: x"},
        {"file": "<b>&\"x\".webp", "repeat": 2, "mode": "composite", "composite_reason": "document<script>",
         "orig": "files/a&b_orig.webp", "result": "files/a&b_result.jpg"},
    ]


def test_write_review_template_and_html(run_mod, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    run_dir = tmp_path / "runs" / "r1"
    run_dir.mkdir(parents=True)
    gt = {"bag.webp": {"photo_type": "product", "wear_level": "light", "key_texts": ["LV", "<Paris>"],
                       "note": "손잡이 <닳음>"}}
    run_mod._write_review("r1", run_dir, _rows(), gt)

    tpl = tmp_path / "reviews" / "r1" / "TEMPLATE.csv"
    with open(tpl, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        assert reader.fieldnames == run_mod.REVIEW_COLS
        rows = list(reader)
    assert [(r["file"], r["repeat"], r["mode"]) for r in rows] == [
        ("bag.webp", "1", "generate"), ('<b>&"x".webp', "2", "composite")]
    assert all(r["reviewed"] == "" and r["note"] == "" for r in rows)

    page = (run_dir / "review.html").read_text(encoding="utf-8")
    assert page.count("<section>") == 2
    assert "boom.webp" not in page                       # 오류 행 제외
    assert "&lt;b&gt;&amp;&quot;x&quot;.webp · repeat 2" in page
    assert '<b>&"x"' not in page
    assert "document&lt;script&gt;" in page and "<script>" not in page
    assert 'src="files/a&amp;b_orig.webp"' in page
    assert "LV, &lt;Paris&gt;" in page
    assert "손잡이 &lt;닳음&gt;" in page
    assert "(-)" in page                                   # composite_reason None → -
    assert "지켜야 할 글자: —" in page                     # gt 없는 사진
    assert "Carret eval r1" in page
    assert "review.html" in capsys.readouterr().out


def test_write_review_reviews_dir_lives_under_here_and_is_idempotent(run_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    run_dir = tmp_path / "x"
    run_dir.mkdir()
    run_mod._write_review("r", run_dir, [], {})
    run_mod._write_review("r", run_dir, [], {})           # 두 번 불러도 mkdir 가 안 터진다
    with open(tmp_path / "reviews" / "r" / "TEMPLATE.csv", encoding="utf-8") as f:
        assert list(csv.reader(f)) == [run_mod.REVIEW_COLS]
    assert "<section>" not in (run_dir / "review.html").read_text(encoding="utf-8")


def test_review_cols_match_report_flags(run_mod, report_mod):
    for col in report_mod.OBJECT_FLAGS + report_mod.QUALITY_FLAGS + ("file", "repeat", "reviewed"):
        assert col in run_mod.REVIEW_COLS


def test_isolate_sets_env(run_mod, tmp_path, monkeypatch):
    for k in ("STORAGE_BACKEND", "STORAGE_DIR", "DB_PATH"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("STORAGE_BACKEND", "gcs")         # 기존 값도 덮어써야 한다 (monkeypatch 가 복원)
    run_mod._isolate(tmp_path)
    assert os.environ["STORAGE_BACKEND"] == "local"
    assert os.environ["STORAGE_DIR"] == str(tmp_path / "storage")
    assert os.environ["DB_PATH"] == str(tmp_path / "carret.db")


def test_confirm_yes_skips_input(run_mod, monkeypatch, capsys):
    def boom(*_):
        raise AssertionError("input 을 부르면 안 된다")
    monkeypatch.setattr("builtins.input", boom)
    assert run_mod._confirm(10, True, True) is True
    out = capsys.readouterr().out
    assert "사진 10번" in out and "$0.40" in out and "전체 파이프라인" in out


def test_confirm_cost_analyze(run_mod, monkeypatch, capsys):
    run_mod._confirm(10, False, True)
    out = capsys.readouterr().out
    assert "$0.05" in out and "analyze 만" in out


@pytest.mark.parametrize("answer,expected", [
    ("y", True), ("Y", True), (" y ", True), ("yes", False), ("", False), ("n", False), ("N", False)])
def test_confirm_input(run_mod, monkeypatch, answer, expected):
    monkeypatch.setattr("builtins.input", lambda *_: answer)
    assert run_mod._confirm(1, False, False) is expected


# ── main: --split · 라벨 안 된 사진 건너뛰기 (_confirm 에서 멈춘다) ──
def _setup_main(run_mod, tmp_path, monkeypatch, entries, argv):
    import json
    import sys
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    monkeypatch.setattr(run_mod, "IMAGES", tmp_path / "images")
    (tmp_path / "images").mkdir()
    for e in entries:
        (tmp_path / "images" / e["file"]).write_bytes(b"x")
    (tmp_path / "dataset.json").write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run.py", *argv])
    calls = []
    monkeypatch.setattr(run_mod, "_confirm", lambda n, full, yes: calls.append((n, full, yes)) or False)
    return calls


def _lab(file, **kw):
    return {"file": file, "photo_type": "product", "wear_level": "none", "text_level": "none", **kw}


def test_main_split_filters_and_skips_unlabeled(run_mod, tmp_path, monkeypatch, capsys):
    entries = [_lab("t1.jpg", split="test"), _lab("t2.jpg", split="test"),
               _lab("d1.jpg", split="dev"), _lab("old.jpg"),                 # split 없음 → dev
               {"file": "t_raw.jpg", "split": "test", "photo_type": "product", "wear_level": "",
                "text_level": "none"}]
    calls = _setup_main(run_mod, tmp_path, monkeypatch, entries, ["--split", "test", "--repeat", "3"])
    assert run_mod.main() == 1                                 # _confirm False 에서 멈춤
    assert calls == [(2 * 3, True, False)]
    assert "라벨이 안 된 사진 1장은 건너뛴다: t_raw.jpg" in capsys.readouterr().out
    assert not (tmp_path / "runs").exists()

    (tmp_path / "b").mkdir()
    calls = _setup_main(run_mod, tmp_path / "b", monkeypatch, entries, ["--split", "dev", "--analyze-only"])
    assert run_mod.main() == 1
    assert calls == [(2, False, False)]                        # d1 + split 없는 old


def test_main_returns_1_when_nothing_left(run_mod, tmp_path, monkeypatch, capsys):
    entries = [_lab("d1.jpg", split="dev"), {"file": "t_raw.jpg", "split": "test"}]
    calls = _setup_main(run_mod, tmp_path, monkeypatch, entries, ["--split", "test", "--yes"])
    assert run_mod.main() == 1
    out = capsys.readouterr().out
    assert "t_raw.jpg" in out and "돌릴 사진이 없다" in out
    assert calls == []
