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



def test_main_stops_before_spending_when_vlm_fails(run_mod, monkeypatch, tmp_path, capsys):
    """한도 초과(429) 같은 실패면 비용을 쓰기 전에 멈춘다."""
    images = tmp_path / "images"
    images.mkdir()
    (images / "a.jpg").write_bytes(b"x")
    (tmp_path / "dataset.json").write_text(
        '[{"file": "a.jpg", "photo_type": "product", "wear_level": "none", "text_level": "none"}]',
        encoding="utf-8")
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    monkeypatch.setattr(run_mod, "IMAGES", images)
    monkeypatch.setattr(run_mod, "_isolate", lambda d: None)
    monkeypatch.setattr(run_mod, "_preflight", lambda det, data: "RuntimeError: 429 RESOURCE_EXHAUSTED")
    monkeypatch.setattr(run_mod.sys, "argv", ["run.py", "--analyze-only", "--yes"])
    assert run_mod.main() == 1
    assert "VLM 호출이 안 된다" in capsys.readouterr().out


def test_preflight_reports_error(run_mod):
    import types
    ok = types.SimpleNamespace(analyze=lambda b: {})
    bad = types.SimpleNamespace(analyze=lambda b: (_ for _ in ()).throw(RuntimeError("429 RESOURCE_EXHAUSTED")))
    assert run_mod._preflight(ok, b"x") is None
    assert "429" in run_mod._preflight(bad, b"x")


# ── 10-01: 실행 조건을 meta 에 ─────────────────
def test_conditions_record_model_steps_lock_and_code(run_mod):
    import hashlib
    from app.prompts.presets import SECONDHAND_LOCK
    c = run_mod._conditions()
    assert c["gen_steps"] == 8 and c["gen_model"] and c["vlm_model"]
    assert c["lock"] == SECONDHAND_LOCK and c["lock_sha"] == hashlib.sha1(SECONDHAND_LOCK.encode()).hexdigest()[:10]
    assert set(c) >= {"commit", "app_dirty", "app_diff_sha"}
    assert (c["app_diff_sha"] is None) == (not c["app_dirty"])


def test_git_without_git_is_empty_not_crash(run_mod, monkeypatch):
    def boom(*a, **k):
        raise OSError("no git")
    monkeypatch.setattr(run_mod.subprocess, "run", boom)
    assert run_mod._git() == {"commit": None, "app_dirty": False, "app_diff_sha": None}


# ── 10-02: failure_tags · --lock-file · prompt_used ──────
def test_review_cols_has_failure_tags_and_matches_grade(run_mod):
    from conftest import _load
    grade = _load("grade")
    assert "failure_tags" in run_mod.REVIEW_COLS
    assert run_mod.REVIEW_COLS == list(grade.COLS)              # TEMPLATE.csv 와 채점 서버가 같은 칸


def test_template_csv_has_failure_tags_blank(run_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    run_dir = tmp_path / "x"
    run_dir.mkdir()
    run_mod._write_review("r", run_dir, _rows(), {})
    with open(tmp_path / "reviews" / "r" / "TEMPLATE.csv", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert rows and all(r["failure_tags"] == "" for r in rows)


def test_write_review_shows_prompt_used_escaped(run_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    run_dir = tmp_path / "x"
    run_dir.mkdir()
    rows = _rows()
    rows[0]["prompt_used"] = 'white bg <script>alert("x")</script> & keep'
    run_mod._write_review("r", run_dir, rows, {})
    page = (run_dir / "review.html").read_text(encoding="utf-8")
    assert page.count("<summary>쓴 프롬프트</summary>") == 2           # 오류 행 제외, 카드마다
    assert 'white bg &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; keep' in page
    assert "<script>" not in page
    assert '<p class="prompt">-</p>' in page                          # prompt_used 없음 → -


@pytest.mark.parametrize("value", [None, ""])
def test_write_review_prompt_used_empty_is_dash(run_mod, tmp_path, monkeypatch, value):
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    run_dir = tmp_path / "x"
    run_dir.mkdir()
    rows = [dict(_rows()[0], prompt_used=value)]
    run_mod._write_review("r", run_dir, rows, {})
    assert '<p class="prompt">-</p>' in (run_dir / "review.html").read_text(encoding="utf-8")


@pytest.fixture
def presets(monkeypatch):
    """presets 의 잠금 두 값을 테스트 뒤 되돌린다."""
    from app.prompts import presets as p
    monkeypatch.setattr(p, "SECONDHAND_LOCK", p.SECONDHAND_LOCK)
    monkeypatch.setattr(p, "LEGACY_LOCKS", p.LEGACY_LOCKS)
    return p


def test_override_lock_replaces_and_moves_old_to_legacy(run_mod, presets):
    old, old_legacy = presets.SECONDHAND_LOCK, presets.LEGACY_LOCKS
    run_mod._override_lock("NEW LOCK.")
    assert presets.SECONDHAND_LOCK == "NEW LOCK."
    assert presets.LEGACY_LOCKS == (*old_legacy, old)


def test_override_lock_with_secondhand_lock_strips_old(run_mod, presets):
    old = presets.SECONDHAND_LOCK
    first_legacy = presets.LEGACY_LOCKS[0] if presets.LEGACY_LOCKS else None
    run_mod._override_lock("NEW LOCK.")
    w = presets.with_secondhand_lock
    assert w("white bg.") == "white bg. NEW LOCK."
    assert w(f"white bg. {old}") == "white bg. NEW LOCK."             # Langfuse 프리셋에 옛 잠금이 있어도 떼어냄
    assert w(f"white bg. NEW LOCK. {old}") == "white bg. NEW LOCK."    # 둘 다 있어도 한 번만
    assert w(old) == "NEW LOCK." and w("") == "NEW LOCK." and w("   ") == "NEW LOCK."
    if first_legacy:
        assert w(f"bg. {first_legacy}") == "bg. NEW LOCK."            # 원래 있던 legacy 도 계속 떼어냄
    assert w("bg.").endswith("NEW LOCK.") and old not in w(f"bg {old} more")


def test_override_lock_twice_keeps_both_old_locks(run_mod, presets):
    orig = presets.SECONDHAND_LOCK
    run_mod._override_lock("A LOCK.")
    run_mod._override_lock("B LOCK.")
    assert presets.SECONDHAND_LOCK == "B LOCK."
    assert presets.LEGACY_LOCKS[-2:] == (orig, "A LOCK.")
    assert presets.with_secondhand_lock(f"bg {orig} A LOCK.") == "bg B LOCK."


def test_override_lock_get_preset_ends_with_new_lock(run_mod, presets, monkeypatch):
    monkeypatch.setattr(presets, "get_prompt_text", lambda name, fallback: f"{fallback} {presets.LEGACY_LOCKS[-1]}")
    run_mod._override_lock("NEW LOCK.")
    p = presets.get_preset("studio_white")["prompt"]
    assert p.endswith(" NEW LOCK.") and p.count("NEW LOCK.") == 1
    assert presets.LEGACY_LOCKS[-1] not in p


def test_override_lock_new_lock_is_substring_of_old(run_mod, presets):
    old = presets.SECONDHAND_LOCK
    new = old.split(". ")[0] + "."
    run_mod._override_lock(new)
    assert presets.with_secondhand_lock(f"bg. {old}") == f"bg. {new}"


# main --lock-file — 실제 VLM 대신 가짜 _preflight · _analyze_row · _conditions
def _setup_lock_main(run_mod, tmp_path, monkeypatch, argv):
    import json
    import sys
    from app.core import db
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    monkeypatch.setattr(run_mod, "IMAGES", tmp_path / "images")
    (tmp_path / "images").mkdir(exist_ok=True)
    (tmp_path / "images" / "a.jpg").write_bytes(b"x")
    (tmp_path / "dataset.json").write_text(json.dumps([_lab("a.jpg")]), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["run.py", *argv])
    monkeypatch.setattr(run_mod, "_isolate", lambda d: None)
    monkeypatch.setattr(db, "init_db", lambda: None)
    monkeypatch.setattr(run_mod, "_preflight", lambda det, data: None)
    monkeypatch.setattr(run_mod, "_conditions", lambda: {})
    seen = []

    def fake_row(det, e, data):
        from app.prompts import presets as p
        seen.append(p.SECONDHAND_LOCK)
        return {"photo_type": "product"}
    monkeypatch.setattr(run_mod, "_analyze_row", fake_row)
    return seen


def test_main_lock_file_overrides_and_records_meta(run_mod, presets, tmp_path, monkeypatch):
    import json
    lock = tmp_path / "lock.txt"
    lock.write_text("  Keep it.\n\n  Do   not\trepair.  \n", encoding="utf-8")
    seen = _setup_lock_main(run_mod, tmp_path, monkeypatch,
                            ["--analyze-only", "--yes", "--run-id", "t", "--lock-file", str(lock)])
    assert run_mod.main() == 0
    assert seen == ["Keep it. Do not repair."]                         # 공백·줄바꿈을 한 칸으로
    meta = json.loads((tmp_path / "runs" / "t" / "meta.json").read_text(encoding="utf-8"))
    assert meta["lock_file"] == str(lock)


def test_main_without_lock_file_meta_none_and_lock_unchanged(run_mod, presets, tmp_path, monkeypatch):
    import json
    orig = presets.SECONDHAND_LOCK
    seen = _setup_lock_main(run_mod, tmp_path, monkeypatch, ["--analyze-only", "--yes", "--run-id", "t"])
    assert run_mod.main() == 0
    assert seen == [orig]
    assert json.loads((tmp_path / "runs" / "t" / "meta.json").read_text(encoding="utf-8"))["lock_file"] is None


def test_main_lock_file_relative_to_eval_dir(run_mod, presets, tmp_path, monkeypatch):
    (tmp_path / "locks").mkdir()
    (tmp_path / "locks" / "a.txt").write_text("REL LOCK.", encoding="utf-8")
    seen = _setup_lock_main(run_mod, tmp_path, monkeypatch,
                            ["--analyze-only", "--yes", "--run-id", "t", "--lock-file", "locks/a.txt"])
    monkeypatch.chdir(tmp_path / "images")                            # cwd 에는 locks/ 가 없다
    assert run_mod.main() == 0
    assert seen == ["REL LOCK."]


@pytest.mark.parametrize("content", ["", "   ", "\n\t \n"])
def test_main_lock_file_empty_returns_1_before_anything(run_mod, tmp_path, monkeypatch, capsys, content):
    import sys
    lock = tmp_path / "empty.txt"
    lock.write_text(content, encoding="utf-8")
    monkeypatch.setattr(run_mod, "HERE", tmp_path)                    # dataset.json 없음 — 그 전에 멈춰야 한다
    monkeypatch.setattr(sys, "argv", ["run.py", "--yes", "--lock-file", str(lock)])
    assert run_mod.main() == 1
    assert "이 비었다" in capsys.readouterr().out
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize("make_dir", [False, True])
def test_main_lock_file_missing_or_directory_returns_1(run_mod, tmp_path, monkeypatch, capsys, make_dir):
    import sys
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    monkeypatch.chdir(tmp_path)
    if make_dir:
        (tmp_path / "locks").mkdir()                                  # 파일이 아니라 디렉터리
    monkeypatch.setattr(sys, "argv", ["run.py", "--yes", "--lock-file", "locks"])
    assert run_mod.main() == 1
    assert "잠금 파일이 없다: locks" in capsys.readouterr().out
    assert not (tmp_path / "runs").exists()
