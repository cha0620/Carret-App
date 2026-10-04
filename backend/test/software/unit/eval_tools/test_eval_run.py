"""eval/run.py — _write_review · _isolate · _confirm (파이프라인·VLM 호출 없음)."""
import csv

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
    run_dir = tmp_path / "results" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    gt = {"bag.webp": {"photo_type": "product", "wear_level": "light", "key_texts": ["LV", "<Paris>"],
                       "note": "손잡이 <닳음>"}}
    run_mod._write_review("r1", run_dir, _rows(), gt)

    tpl = tmp_path / "results" / "reviews" / "r1" / "TEMPLATE.csv"
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


@pytest.mark.parametrize("answer,expected", [
    ("y", True), ("Y", True), (" y ", True),])
def test_confirm_input(run_mod, monkeypatch, answer, expected):
    monkeypatch.setattr("builtins.input", lambda *_: answer)
    assert run_mod._confirm(1, False, False) is expected


# ── main: --split · 라벨 안 된 사진 건너뛰기 (_confirm 에서 멈춘다) ──
def _setup_main(run_mod, tmp_path, monkeypatch, entries, argv):
    import json
    import sys
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    monkeypatch.setattr(run_mod, "IMAGES", tmp_path / "data" / "images")
    (tmp_path / "data" / "images").mkdir(parents=True)
    for e in entries:
        (tmp_path / "data" / "images" / e["file"]).write_bytes(b"x")
    (tmp_path / "data" / "dataset.json").write_text(json.dumps(entries), encoding="utf-8")
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
    assert not (tmp_path / "results" / "runs").exists()

    (tmp_path / "b").mkdir()
    calls = _setup_main(run_mod, tmp_path / "b", monkeypatch, entries, ["--split", "dev", "--analyze-only"])
    assert run_mod.main() == 1
    assert calls == [(2, False, False)]                        # d1 + split 없는 old


def test_main_stops_before_spending_when_vlm_fails(run_mod, monkeypatch, tmp_path, capsys):
    """한도 초과(429) 같은 실패면 비용을 쓰기 전에 멈춘다."""
    images = tmp_path / "data" / "images"
    images.mkdir(parents=True)
    (images / "a.jpg").write_bytes(b"x")
    (tmp_path / "data" / "dataset.json").write_text(
        '[{"file": "a.jpg", "photo_type": "product", "wear_level": "none", "text_level": "none"}]',
        encoding="utf-8")
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    monkeypatch.setattr(run_mod, "IMAGES", images)
    monkeypatch.setattr(run_mod, "_isolate", lambda d: None)
    monkeypatch.setattr(run_mod, "_preflight", lambda det, data: "RuntimeError: 429 RESOURCE_EXHAUSTED")
    monkeypatch.setattr(run_mod.sys, "argv", ["run.py", "--analyze-only", "--yes"])
    assert run_mod.main() == 1
    assert "VLM 호출이 안 된다" in capsys.readouterr().out


# ── 10-01: 실행 조건을 meta 에 ─────────────────
def test_conditions_record_model_steps_lock_and_code(run_mod):
    import hashlib
    from app.prompts.presets import SECONDHAND_LOCK
    c = run_mod._conditions()
    assert c["gen_steps"] == 8 and c["gen_model"] and c["vlm_model"]
    assert c["lock"] == SECONDHAND_LOCK and c["lock_sha"] == hashlib.sha1(SECONDHAND_LOCK.encode()).hexdigest()[:10]
    assert set(c) >= {"commit", "app_dirty", "app_diff_sha"}
    assert (c["app_diff_sha"] is None) == (not c["app_dirty"])


def test_write_review_shows_prompt_used_escaped(run_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    run_dir = tmp_path / "x"
    run_dir.mkdir(parents=True)
    rows = _rows()
    rows[0]["prompt_used"] = 'white bg <script>alert("x")</script> & keep'
    run_mod._write_review("r", run_dir, rows, {})
    page = (run_dir / "review.html").read_text(encoding="utf-8")
    assert page.count("<summary>쓴 프롬프트</summary>") == 2           # 오류 행 제외, 카드마다
    assert 'white bg &lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt; &amp; keep' in page
    assert "<script>" not in page
    assert '<p class="prompt">-</p>' in page                          # prompt_used 없음 → -


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


# main --lock-file — 실제 VLM 대신 가짜 _preflight · _analyze_row · _conditions
def _setup_lock_main(run_mod, tmp_path, monkeypatch, argv):
    import json
    import sys
    from app.core import db
    monkeypatch.setattr(run_mod, "HERE", tmp_path)
    monkeypatch.setattr(run_mod, "IMAGES", tmp_path / "data" / "images")
    (tmp_path / "data" / "images").mkdir(exist_ok=True)
    (tmp_path / "data" / "images" / "a.jpg").write_bytes(b"x")
    (tmp_path / "data" / "dataset.json").write_text(json.dumps([_lab("a.jpg")]), encoding="utf-8")
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
    meta = json.loads((tmp_path / "results" / "runs" / "t" / "meta.json").read_text(encoding="utf-8"))
    assert meta["lock_file"] == "lock.txt"                              # 경로가 아니라 파일 이름만 (기계마다 달라 비교 오탐)


@pytest.mark.parametrize("content", ["", "   ", "\n\t \n"])
def test_main_lock_file_empty_returns_1_before_anything(run_mod, tmp_path, monkeypatch, capsys, content):
    import sys
    lock = tmp_path / "empty.txt"
    lock.write_text(content, encoding="utf-8")
    monkeypatch.setattr(run_mod, "HERE", tmp_path)                    # dataset.json 없음 — 그 전에 멈춰야 한다
    monkeypatch.setattr(sys, "argv", ["run.py", "--yes", "--lock-file", str(lock)])
    assert run_mod.main() == 1
    assert "이 비었다" in capsys.readouterr().out
    assert not (tmp_path / "results" / "runs").exists()


# ── select ──────────────────────────────────────
def _ds():
    return [{"file": "a.webp", "split": "dev"}, {"file": "b.webp", "split": "test"},
            {"file": "c.webp"}, {"file": "d.webp", "split": None, "set": "failure"},
            {"file": "e.webp", "split": "test", "set": "failure"}]


def _files(xs):
    return [e["file"] for e in xs]


def test_select_conditions_stack(run_mod):
    assert _files(run_mod.select(_ds(), only="a.webp,d.webp,e.webp", split="dev", set_="failure")) == ["d.webp"]
    assert run_mod.select(_ds(), only="a.webp", set_="failure") == []


# ── dataset_sha ─────────────────────────────────
def test_dataset_sha_order_independent_and_stable(run_mod, tmp_path):
    (tmp_path / "a.webp").write_bytes(b"A")
    (tmp_path / "b.webp").write_bytes(b"B")
    es = [{"file": "a.webp", "wear_level": "light"}, {"file": "b.webp", "photo_type": "product"}]
    sha = run_mod.dataset_sha(es, tmp_path)
    assert len(sha) == 10 and all(c in "0123456789abcdef" for c in sha)
    assert run_mod.dataset_sha(list(reversed(es)), tmp_path) == sha
    # 항목 안 키 순서도 상관없다
    assert run_mod.dataset_sha([{"wear_level": "light", "file": "a.webp"}, es[1]], tmp_path) == sha


@pytest.mark.parametrize("key", ["photo_type", "wear_level", "text_level",])
def test_dataset_sha_every_answer_label_counts(run_mod, tmp_path, key):
    assert key in run_mod.DATASET_LABELS
    base = run_mod.dataset_sha([{"file": "a.webp"}], tmp_path)
    assert run_mod.dataset_sha([{"file": "a.webp", key: "x"}], tmp_path) != base


@pytest.mark.parametrize("extra", [{"note": "x"}, {"labeled_by": "kim"}, {"set": "failure"},])
def test_dataset_sha_ignores_non_answer_fields(run_mod, tmp_path, extra):
    (tmp_path / "a.webp").write_bytes(b"A")
    base = run_mod.dataset_sha([{"file": "a.webp", "wear_level": "light"}], tmp_path)
    assert run_mod.dataset_sha([{"file": "a.webp", "wear_level": "light", **extra}], tmp_path) == base


# ══ 10-03: _full_row 가 dataset 정답 개수를 answer_count 로 넘긴다 ═══════
@pytest.fixture
def full_row(run_mod, tmp_path, monkeypatch, make_png):
    """파이프라인을 가짜로 — run_transform 이 받은 인자를 기록하고 결과 이미지만 저장.
    st.prompt 로 돌려줄 prompt_used 를 정한다."""
    import types
    from app.services import pipeline
    from app.services.persistence import storage
    images, files = tmp_path / "data" / "images", tmp_path / "files"
    images.mkdir(parents=True)
    files.mkdir(parents=True)
    png = make_png()
    (images / "cd.png").write_bytes(png)
    monkeypatch.setattr(run_mod, "IMAGES", images)
    st = types.SimpleNamespace(calls=[], prompt="P")

    def run_transform(fid, preset, **kw):
        st.calls.append(kw)
        storage.save("result", f"{fid}_{preset}.jpg", png)
        return {"prompt_used": st.prompt}

    monkeypatch.setattr(pipeline, "run_transform", run_transform)
    st.call = lambda entry, composition=None: run_mod._full_row(entry, png, 1, "studio_white", files, composition)
    return st


def test_full_row_composition_recorded_only_when_in_prompt(full_row):
    from app.services.compositions import BY_KEY
    full_row.prompt = "bg " + BY_KEY["shoes_side"]["prompt"] + " lock"
    assert full_row.call({"file": "cd.png"}, "shoes_side")["composition"] == "shoes_side"
    full_row.prompt = "bg lock"                          # 파이프라인이 뺐다 (물건 여러 개 등)
    assert full_row.call({"file": "cd.png", "item_count": 2}, "shoes_side")["composition"] is None


def test_main_skips_extras_unless_named(run_mod, tmp_path, monkeypatch, capsys):
    entries = [_lab("a_p01.jpg", split="dev", post="a", post_index=1),
               _lab("a_p02.jpg", split="dev", post="a", post_index=2),      # 라벨이 있어도 기본은 빠진다
               _lab("old.jpg", split="dev")]
    calls = _setup_main(run_mod, tmp_path, monkeypatch, entries, ["--analyze-only"])
    assert run_mod.main() == 1
    assert calls == [(2, False, False)]
    assert "a_p02.jpg" not in capsys.readouterr().out

    (tmp_path / "b").mkdir()
    calls = _setup_main(run_mod, tmp_path / "b", monkeypatch, entries, ["--analyze-only", "--only", "a_p02.jpg"])
    assert run_mod.main() == 1
    assert calls == [(1, False, False)]
