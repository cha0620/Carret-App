"""eval/compare.py — 두 실행 비교 (판정 합치기 · 요약 · 경고 · 조건/문구 차이 · 정렬 · HTML · main)."""
import csv
import json

import pytest



# ── 도우미 ──────────────────────────────────────
def V(preserved=True, clean=True, quality=None, tags=()):
    return {"preserved": preserved, "clean": clean, "quality": quality, "tags": list(tags), "flags": []}


def R(file, repeat=1, **kw):
    row = {"file": file, "repeat": repeat, "mode": "generate"}
    row.update(kw)
    return row


def write_run(root, run_id, results, meta=None):
    d = root / "results" / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results),
                                     encoding="utf-8")
    if meta is not None:
        (d / "meta.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def write_review(root, run_id, rater, rows):
    d = root / "results" / "reviews" / run_id
    d.mkdir(parents=True, exist_ok=True)
    cols = ["file", "repeat", "mode", "reviewed", "shape_color_changed", "text_changed", "wear_changed",
            "added_content", "background_issue", "framing_issue", "failure_tags", "photo_quality", "note"]
    with open(d / f"{rater}.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for r in rows:
            w.writerow({c: r.get(c, "") for c in cols})


# ── verdicts ────────────────────────────────────
def test_verdicts_majority_and_tie_is_fail(compare_mod):
    results = [R("a.webp"), R("b.webp")]
    reviews = {
        "x": {("a.webp", 1): V(True, True, 5, ["t1"]), ("b.webp", 1): V(True, True, 4)},
        "y": {("a.webp", 1): V(True, False, 3, ["t2"]), ("b.webp", 1): V(False, False, None)},
        "z": {("a.webp", 1): V(False, False, None, ["t1"])},
    }
    v = compare_mod.verdicts(results, reviews)
    a = v[("a.webp", 1)]
    assert a["preserved"] is True and a["clean"] is False       # 2:1, 1:2
    assert a["quality"] == 4.0                                  # None 은 평균에서 빠짐
    assert a["tags"] == ["t1", "t2"] and a["n"] == 3
    b = v[("b.webp", 1)]
    assert b["preserved"] is False and b["clean"] is False      # 1:1 동점은 실패
    assert b["quality"] == 4.0 and b["n"] == 2


# ── summarize ───────────────────────────────────
def test_summarize_counts(compare_mod):
    results = [
        R("a", 1, gate_passed=True, judge={"trust": 0.8}, item_similarity=0.9, elapsed_s=10),
        R("a", 2, gate_passed=False, judge={"trust": 0.4}, elapsed_s=30),
        R("b", 1, mode="composite", elapsed_s=20, gate_passed=True),   # 생성본 아니라 게이트에 안 셈
        R("c", 1, error="x", elapsed_s=999),
        R("d", 1, gate_passed=None, elapsed_s="?"),
    ]
    verdict = {("a", 1): {"preserved": True, "clean": True, "quality": 4.0, "tags": [], "n": 1},
               ("a", 2): {"preserved": False, "clean": False, "quality": 5.0, "tags": [], "n": 1},
               ("b", 1): {"preserved": True, "clean": False, "quality": 3.0, "tags": [], "n": 1},
               ("d", 1): {"preserved": True, "clean": True, "quality": None, "tags": [], "n": 1}}
    s = compare_mod.summarize(results, verdict)
    assert s["runs"] == 5 and s["errors"] == 1
    assert s["modes"]["generate"] == 3 and s["modes"]["composite"] == 1
    assert s["gate"] == (1, 2)
    assert s["trust"] == pytest.approx(0.6)
    assert s["item_dino"] == pytest.approx(0.9)
    assert s["elapsed"] == 20                    # 오류 행 · 숫자 아닌 값 제외
    assert s["reviewed"] == (4, 4)
    assert s["preserved"] == (3, 4) and s["clean"] == (2, 4)
    # 바로 쓸 수 있음 = 보존 + 품질 >= 4, 품질 없는 건 분모에서도 빠짐
    assert s["usable"] == (1, 3)
    assert s["quality"] == pytest.approx(4.0)
    # 물건 기준 매번 통과: a(통과·실패) ✗, b ✓, d ✓
    assert s["every_item"] == (2, 3)


def test_summarize_every_item_needs_all_repeats_reviewed(compare_mod):
    results = [R("a", 1), R("a", 2), R("b", 1), R("b", 2, error="x"), R("c", 1)]
    verdict = {("a", 1): V(True), ("b", 1): V(True), ("c", 1): V(False)}
    s = compare_mod.summarize(results, verdict)
    # a 는 r2 채점 없음 → 제외, b 는 r2 가 오류라 r1 만으로 판단, c 는 실패
    assert s["every_item"] == (1, 2)


def test_warnings_files_differ(compare_mod):
    m = {"repeat": 1, "dataset_sha": "s"}
    w = compare_mod.warnings(m, m, [R("a"), R("b")], [R("a"), R("c")], {}, {})
    assert any("사진이 다르다" in x and "기준에만 b" in x and "실험에만 c" in x for x in w)


def test_warnings_dataset_sha_none_value_vs_missing(compare_mod):
    """칸이 있고 값이 None 인 것과 칸이 없는 것은 다르다 — 한쪽이라도 칸이 없으면 '기록 없음'."""
    w = compare_mod.warnings({"repeat": 1, "dataset_sha": None}, {"repeat": 1}, [], [], {}, {})
    assert any("기록이 없는" in x and "dataset_sha" in x for x in w)
    w = compare_mod.warnings({"repeat": 1, "dataset_sha": "a"}, {"repeat": 1, "dataset_sha": "b"}, [], [], {}, {})
    assert w == ["데이터셋 버전(dataset_sha)이 다르다 — 라벨이나 사진이 바뀌었다"]


def test_warnings_raters_differ(compare_mod):
    m = {"repeat": 1, "dataset_sha": "s"}
    rb = [R("a")]
    v = {("a", 1): V()}
    w = compare_mod.warnings(m, m, rb, rb, v, v, ["kim", "lee"], ["kim"])
    assert any(x.startswith("평가자가 다르다 (기준 kim, lee · 실험 kim)") for x in w)
    w = compare_mod.warnings(m, m, rb, rb, {}, v, [], ["kim"])
    assert any("기준 없음 · 실험 kim" in x for x in w)
    # 채점이 하나도 없으면 평가자 경고는 안 낸다
    assert not any("평가자" in x for x in compare_mod.warnings(m, m, [], [], {}, {}, ["kim"], []))


# ── condition_diff ──────────────────────────────
def test_condition_diff_only_changed_known_keys(compare_mod):
    mb = {"gen_model": "m1", "lock_sha": "x", "repeat": 1, "unrelated": 1}
    me = {"gen_model": "m1", "lock_sha": "y", "note": "trim", "unrelated": 2}
    d = compare_mod.condition_diff(mb, me)
    assert d == [("lock_sha", "x", "y"), ("repeat", 1, "(기록 없음)"), ("note", "(기록 없음)", "trim")]
    assert compare_mod.condition_diff(mb, dict(mb)) == []


# ── file_order ──────────────────────────────────
def test_file_order_worse_better_same_unrated(compare_mod):
    vb = {("worse", 1): V(True), ("better", 1): V(False), ("same", 1): V(True),
          ("only_b", 1): V(True), ("worse2", 1): V(True), ("worse2", 2): V(True)}
    ve = {("worse", 1): V(False), ("better", 1): V(True), ("same", 1): V(True),
          ("worse2", 1): V(True), ("worse2", 2): V(False)}
    files = ["same", "only_b", "better", "none", "worse2", "worse"]
    assert compare_mod.file_order(files, vb, ve) == ["worse", "worse2", "better", "same", "none", "only_b"]


@pytest.mark.parametrize("rel", ["", None, "/etc/passwd",])
def test_src_rejects_empty_absolute_and_parent(compare_mod, rel):
    assert compare_mod.src("r", rel) is None


# ── build_html ──────────────────────────────────
def test_build_html_escapes_everything(compare_mod):
    evil = "<script>alert(1)</script>"
    rb = [R(f"{evil}.webp", 1, orig="files/o.webp", result="files/r.jpg"),
          R("e.webp", 1, error=evil)]
    re_ = [R(f"{evil}.webp", 1, result="files/r2.jpg")]
    vb = {(f"{evil}.webp", 1): V(True, tags=[evil])}
    ve = {(f"{evil}.webp", 1): V(False)}
    mb = {"lock": f"Keep {evil}. Same.", "note": evil, "repeat": 1}
    me = {"lock": "Same. New one.", "repeat": 1}
    h = compare_mod.build_html("b<x>", "e&y", mb, me, rb, re_, vb, ve, rater="<r>")
    assert "<script>" not in h
    assert "&lt;script&gt;" in h
    assert "b&lt;x&gt;" in h and "e&amp;y" in h and "&lt;r&gt;" in h
    assert 'class="old"' in h and 'class="new"' in h       # 잠금 문구 차이
    assert "바뀐 조건" in h and "<td>note</td>" in h
    assert "그대로 비교하면 안 되는 점" in h                  # 사진 다름 (e.webp)
    assert 'src="b%3Cx%3E/files/o.webp"' in h                  # 원본은 기준 실행 폴더에서


def test_main_end_to_end(compare_mod, tmp_path, capsys):
    write_run(tmp_path, "base", [R("a", 1, result="files/a.jpg"), R("b", 1), R("x", 1, error="boom")],
              {"repeat": 1, "dataset_sha": "s1", "lock": "Keep it.", "lock_sha": "l1"})
    write_run(tmp_path, "exp", [R("a", 1), R("b", 1)],
              {"repeat": 1, "dataset_sha": "s1", "lock": "Keep it. More.", "lock_sha": "l2"})
    write_review(tmp_path, "base", "kim", [{"file": "a", "repeat": 1, "reviewed": "1", "photo_quality": "5"},
                                           {"file": "b", "repeat": 1, "reviewed": "1", "text_changed": "1"}])
    write_review(tmp_path, "base", "lee", [{"file": "a", "repeat": 1, "reviewed": "1", "wear_changed": "y"},
                                           {"file": "b", "repeat": 1, "reviewed": "1"}])
    write_review(tmp_path, "exp", "kim", [{"file": "a", "repeat": 1, "reviewed": "1", "photo_quality": "4"},
                                          {"file": "b", "repeat": 1, "reviewed": "1"}])
    write_review(tmp_path, "exp", "TEMPLATE", [{"file": "a", "repeat": 1, "reviewed": "1", "text_changed": "1"}])

    assert compare_mod.main(["base", "exp"]) == 0
    out = capsys.readouterr().out
    page = tmp_path / "results" / "runs" / "compare-base-vs-exp.html"
    assert page.exists()
    assert "사진이 다르다" in out and "기준에만 x" in out
    assert "평가자가 다르다 (기준 kim, lee · 실험 kim)" in out        # TEMPLATE.csv 는 평가자가 아니다
    # 기준: 두 평가자 각각 a·b 하나씩 실패 → 동점 → 둘 다 실패 0/2, 실험 2/2
    assert "| **보존 통과** (사람) | 0/2 (0%) | 2/2 (100%) |" in out
    assert "- lock_sha: l1 → l2" in out
    assert f"페이지: {page}" in out
    assert 'class="new"' in page.read_text(encoding="utf-8")


def test_main_unknown_rater_exits_without_page(compare_mod, tmp_path):
    write_run(tmp_path, "b", [R("a")], {})
    write_run(tmp_path, "e", [R("a")], {})
    write_review(tmp_path, "b", "kim", [{"file": "a", "repeat": 1, "reviewed": "1"}])
    with pytest.raises(SystemExit) as e:
        compare_mod.main(["b", "e", "--rater", "<nobody>"])
    assert "kim" in str(e.value)
    assert not list((tmp_path / "results" / "runs").glob("compare-*.html"))


