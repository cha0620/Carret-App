"""eval/compare.py — 두 실행 비교 (판정 합치기 · 요약 · 경고 · 조건/문구 차이 · 정렬 · HTML · main)."""
import csv
import json
import sys

import pytest

from conftest import EVAL_DIR


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


# ── import 방식 ─────────────────────────────────
def test_import_puts_eval_dir_on_sys_path_and_uses_same_report(compare_mod):
    assert str(EVAL_DIR) in sys.path            # compare 가 넣는다 (fixture 가 테스트 뒤 되돌림)
    assert compare_mod.rp.__file__ == str(EVAL_DIR / "report.py")
    assert compare_mod.rp.USABLE_QUALITY == 4


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


def test_verdicts_all_quality_none_is_none(compare_mod):
    v = compare_mod.verdicts([R("a")], {"x": {("a", 1): V(quality=None)}})
    assert v[("a", 1)]["quality"] is None


def test_verdicts_single_rater(compare_mod):
    reviews = {"x": {("a", 1): V(False)}, "y": {("a", 1): V(True)}, "z": {("a", 1): V(True)}}
    v = compare_mod.verdicts([R("a")], reviews, rater="x")
    assert v[("a", 1)]["preserved"] is False and v[("a", 1)]["n"] == 1


def test_verdicts_unknown_rater_gives_nothing(compare_mod):
    assert compare_mod.verdicts([R("a")], {"x": {("a", 1): V()}}, rater="nobody") == {}


def test_verdicts_skips_error_rows_and_unreviewed(compare_mod):
    results = [R("a", error="boom"), R("b"), R("c")]
    reviews = {"x": {("a", 1): V(), ("b", 1): V()}}
    v = compare_mod.verdicts(results, reviews)
    assert set(v) == {("b", 1)}


def test_verdicts_repeat_keys_distinct(compare_mod):
    reviews = {"x": {("a", 1): V(True), ("a", 2): V(False)}}
    v = compare_mod.verdicts([R("a", 1), R("a", 2)], reviews)
    assert v[("a", 1)]["preserved"] is True and v[("a", 2)]["preserved"] is False


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


def test_summarize_usable_boundary(compare_mod):
    q = compare_mod.rp.USABLE_QUALITY
    verdict = {("a", 1): {"preserved": True, "quality": q, "clean": True},
               ("b", 1): {"preserved": True, "quality": q - 0.5, "clean": True},
               ("c", 1): {"preserved": False, "quality": 5, "clean": False}}
    s = compare_mod.summarize([R("a"), R("b"), R("c")], verdict)
    assert s["usable"] == (1, 3)


def test_summarize_empty(compare_mod):
    s = compare_mod.summarize([], {})
    assert s["runs"] == 0 and s["elapsed"] is None and s["trust"] is None and s["quality"] is None
    assert s["usable"] == (0, 0) and s["every_item"] == (0, 0) and s["gate"] == (0, 0)


# ── warnings ────────────────────────────────────
def test_warnings_none_when_same(compare_mod):
    m = {"repeat": 2, "dataset_sha": "abc"}
    rb = [R("a")]
    assert compare_mod.warnings(m, dict(m), rb, [R("a")], {("a", 1): V()}, {("a", 1): V()}) == []


def test_warnings_files_differ(compare_mod):
    m = {"repeat": 1, "dataset_sha": "s"}
    w = compare_mod.warnings(m, m, [R("a"), R("b")], [R("a"), R("c")], {}, {})
    assert any("사진이 다르다" in x and "기준에만 b" in x and "실험에만 c" in x for x in w)


def test_warnings_files_only_one_side(compare_mod):
    m = {"repeat": 1, "dataset_sha": "s"}
    w = compare_mod.warnings(m, m, [R("a"), R("b")], [R("a")], {}, {})
    msg = next(x for x in w if "사진이 다르다" in x)
    assert "기준에만 b" in msg and "실험에만" not in msg and " ·  " not in msg


def test_warnings_repeat_differs(compare_mod):
    w = compare_mod.warnings({"repeat": 1, "dataset_sha": "s"}, {"repeat": 3, "dataset_sha": "s"}, [], [], {}, {})
    assert w == ["repeat 이 다르다 (1 vs 3)"]


def test_warnings_dataset_sha_differs_or_missing_one_side(compare_mod):
    for mb, me in (({"dataset_sha": "a"}, {"dataset_sha": "b"}), ({"dataset_sha": "a"}, {})):
        w = compare_mod.warnings(mb, me, [], [], {}, {})
        assert any("dataset_sha" in x for x in w)


def test_warnings_dataset_sha_missing_both_sides(compare_mod):
    w = compare_mod.warnings({"repeat": 1}, {"repeat": 1}, [], [], {}, {})
    assert w == ["데이터셋 버전(dataset_sha) 기록이 없는 실행이 있다 — 같은 라벨로 돌렸는지 모른다"]


def test_warnings_dataset_sha_none_value_vs_missing(compare_mod):
    """칸이 있고 값이 None 인 것과 칸이 없는 것은 다르다 — 한쪽이라도 칸이 없으면 '기록 없음'."""
    w = compare_mod.warnings({"repeat": 1, "dataset_sha": None}, {"repeat": 1}, [], [], {}, {})
    assert any("기록이 없는" in x and "dataset_sha" in x for x in w)
    w = compare_mod.warnings({"repeat": 1, "dataset_sha": "a"}, {"repeat": 1, "dataset_sha": "b"}, [], [], {}, {})
    assert w == ["데이터셋 버전(dataset_sha)이 다르다 — 라벨이나 사진이 바뀌었다"]


def test_warnings_repeat_missing(compare_mod):
    w = compare_mod.warnings({"dataset_sha": "s"}, {"repeat": 1, "dataset_sha": "s"}, [], [], {}, {})
    assert w == ["repeat 기록이 없는 실행이 있다 (meta.json)"]


def test_warnings_analyze_only_run(compare_mod):
    m = {"repeat": 1, "dataset_sha": "s"}
    w = compare_mod.warnings({**m, "full": False}, {**m, "full": True}, [], [], {}, {})
    assert w == ["기준 은 --analyze-only 실행 — 생성 결과 · 사람 채점이 없다"]
    assert compare_mod.warnings(m, m, [], [], {}, {}) == []         # full 칸이 없으면 경고 안 함


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


def test_warnings_unreviewed(compare_mod):
    m = {"repeat": 1, "dataset_sha": "s"}
    rb = [R("a"), R("b"), R("c", error="x")]
    w = compare_mod.warnings(m, m, rb, rb, {("a", 1): V()}, {("a", 1): V(), ("b", 1): V()})
    assert "기준 사람 채점 1/2 — 채점이 덜 됐다" in w
    assert not any(x.startswith("실험 사람 채점") for x in w)     # 오류 행은 분모에서 빠짐


# ── condition_diff ──────────────────────────────
def test_condition_diff_only_changed_known_keys(compare_mod):
    mb = {"gen_model": "m1", "lock_sha": "x", "repeat": 1, "unrelated": 1}
    me = {"gen_model": "m1", "lock_sha": "y", "note": "trim", "unrelated": 2}
    d = compare_mod.condition_diff(mb, me)
    assert d == [("lock_sha", "x", "y"), ("repeat", 1, "(기록 없음)"), ("note", "(기록 없음)", "trim")]
    assert compare_mod.condition_diff(mb, dict(mb)) == []


def test_condition_diff_none_vs_missing(compare_mod):
    assert compare_mod.condition_diff({"note": None}, {}) == [("note", None, "(기록 없음)")]
    assert compare_mod.condition_diff({"note": None}, {"note": None}) == []


# ── sentences / lock_diff ───────────────────────
def test_sentences(compare_mod):
    assert compare_mod.sentences("Keep it.  Do not   change!\nReally? yes") == \
        ["Keep it.", "Do not change!", "Really?", "yes"]
    assert compare_mod.sentences("") == [] and compare_mod.sentences(None) == []
    assert compare_mod.sentences("   ") == []
    assert compare_mod.sentences("v1.2 keeps") == ["v1.2 keeps"]     # 공백 없는 점은 안 자른다


def test_lock_diff(compare_mod):
    d = compare_mod.lock_diff("A. B. C.", "A. B2. C. D.")
    assert d == [(" ", "A."), ("-", "B."), ("+", "B2."), (" ", "C."), ("+", "D.")]
    assert all(op in " -+" for op, _ in compare_mod.lock_diff("Keep logo. Same.", "Keep logos. Same."))


def test_lock_diff_same_and_empty(compare_mod):
    assert all(op == " " for op, _ in compare_mod.lock_diff("A. B.", "A.  B."))
    assert compare_mod.lock_diff("", "") == []
    assert compare_mod.lock_diff("", "New.") == [("+", "New.")]


# ── file_order ──────────────────────────────────
def test_file_order_worse_better_same_unrated(compare_mod):
    vb = {("worse", 1): V(True), ("better", 1): V(False), ("same", 1): V(True),
          ("only_b", 1): V(True), ("worse2", 1): V(True), ("worse2", 2): V(True)}
    ve = {("worse", 1): V(False), ("better", 1): V(True), ("same", 1): V(True),
          ("worse2", 1): V(True), ("worse2", 2): V(False)}
    files = ["same", "only_b", "better", "none", "worse2", "worse"]
    assert compare_mod.file_order(files, vb, ve) == ["worse", "worse2", "better", "same", "none", "only_b"]


def test_file_order_empty(compare_mod):
    assert compare_mod.file_order([], {}, {}) == []


# ── summary_table / _md_to_html_table ───────────
def test_summary_table_shape(compare_mod):
    s = compare_mod.summarize([], {})
    md = compare_mod.summary_table("b1", "e1", s, s)
    lines = md.splitlines()
    assert lines[0] == "| | 기준 `b1` | 실험 `e1` |" and lines[1] == "|---|---|---|"
    assert len(lines) == 14
    assert "| 소요 시간 중앙값 | — | — |" in lines
    assert "| 경로 | generate 0 · composite 0 · original 0 | generate 0 · composite 0 · original 0 |" in lines


def test_summary_table_values(compare_mod):
    results = [R("a", elapsed_s=12.34, gate_passed=True)]
    v = {("a", 1): {"preserved": True, "clean": True, "quality": 5.0, "tags": [], "n": 1}}
    md = compare_mod.summary_table("b", "e", compare_mod.summarize(results, v), compare_mod.summarize([], {}))
    assert "| **보존 통과** (사람) | 1/1 (100%) | — |" in md
    assert "| 소요 시간 중앙값 | 12.3s | — |" in md


def test_summary_table_run_id_with_pipe_and_backtick(compare_mod):
    s = compare_mod.summarize([], {})
    md = compare_mod.summary_table("a|b", "c`d", s, s)
    head = md.splitlines()[0]
    assert head == "| | 기준 `a/b` | 실험 `c'd` |"
    h = compare_mod._md_to_html_table(md)
    assert h.splitlines()[1].count("<th>") == 3 and "<code>a/b</code>" in h


def test_md_to_html_table_escapes_and_formats(compare_mod):
    md = "| | 기준 `<b>` | x |\n|---|---|---|\n| **굵게** | <script>alert(1)</script> | a&b |"
    h = compare_mod._md_to_html_table(md)
    assert h.startswith("<table>") and h.endswith("</table>")
    assert "<th>기준 <code>&lt;b&gt;</code></th>" in h
    assert "<td><b>굵게</b></td>" in h
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in h and "<script>" not in h
    assert "a&amp;b" in h and "|---" not in h


def test_md_to_html_table_run_id_escaped(compare_mod):
    s = compare_mod.summarize([], {})
    h = compare_mod._md_to_html_table(compare_mod.summary_table("<img src=x>", "e", s, s))
    assert "<img" not in h and "&lt;img src=x&gt;" in h


# ── _badge / _cell ──────────────────────────────
def test_badge_variants(compare_mod):
    b = compare_mod._badge
    assert "없음" in b(None, None)
    err = b({"error": "<script>" + "x" * 100}, None)
    assert "&lt;script&gt;" in err and "<script>" not in err and 'class="badge bad"' in err
    assert "채점 전" in b({"mode": "<i>g</i>"}, None) and "&lt;i&gt;" in b({"mode": "<i>g</i>"}, None)
    assert "?" in b({}, None)
    good = b({"mode": "generate"}, V(True, quality=4.25, tags=["<t>", "a&b"]))
    assert 'class="badge good"' in good and "품질 4.2" in good
    assert "&lt;t&gt;, a&amp;b" in good and "<t>" not in good
    bad = b({"mode": "generate"}, V(False))
    assert 'class="badge bad"' in bad and "품질" not in bad and "tags" not in bad


def test_badge_error_truncated_before_escape(compare_mod):
    out = compare_mod._badge({"error": "&" * 100}, None)
    assert out.count("&amp;") == 60


@pytest.mark.parametrize("rel", ["", None, "/etc/passwd", "\\x.jpg", "../x.jpg", "files/../../x.jpg"])
def test_src_rejects_empty_absolute_and_parent(compare_mod, rel):
    assert compare_mod.src("r", rel) is None


def test_src_encodes(compare_mod):
    assert compare_mod.src('r"1', "files/<a>&.jpg") == "r%221/files/%3Ca%3E%26.jpg"
    assert compare_mod.src("r", "files/x y.jpg") == "r/files/x%20y.jpg"
    assert compare_mod.src("r", "files/a..b.jpg") == "r/files/a..b.jpg"     # 이름 속 .. 은 괜찮다


def test_cell(compare_mod):
    c = compare_mod._cell('r"1', {"result": "files/<a>&.jpg"}, None, label="<기준> r1")
    assert '<img src="r%221/files/%3Ca%3E%26.jpg"' in c and "<a>" not in c.replace('<a href', '')
    assert '<div class="who">&lt;기준&gt; r1</div>' in c
    assert "<img" not in compare_mod._cell("r", {"result": "../../secret.jpg"}, None)
    assert "who" not in compare_mod._cell("r", None, None)
    assert "<img" not in compare_mod._cell("r", {"result": "x.jpg", "error": "e"}, None)
    assert "<img" not in compare_mod._cell("r", {"mode": "generate"}, None)
    assert "<img" not in compare_mod._cell("r", None, None)


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


def test_build_html_same_conditions_and_lock(compare_mod):
    m = {"lock": "A. B.", "repeat": 1, "dataset_sha": "s"}
    rb = [R("a", result="r.jpg")]
    v = {("a", 1): V()}
    h = compare_mod.build_html("b", "e", m, dict(m), rb, [R("a", result="r.jpg")], v, v, None)
    assert "잠금 문구가 같다" in h and "meta.json 조건이 같다" in h
    assert "그대로 비교하면 안 되는 점" not in h
    assert "모든 평가자 다수결" in h


def test_build_html_orig_prefers_base(compare_mod):
    h = compare_mod.build_html("b", "e", {}, {}, [R("a", orig="ob.webp")], [R("a", orig="oe.webp")], {}, {}, None)
    assert 'src="b/ob.webp"' in h and "oe.webp" not in h


def test_build_html_lock_missing(compare_mod):
    h = compare_mod.build_html("b", "e", {"lock": "A."}, {}, [], [], {}, {}, None)
    assert "실험 실행에 잠금 문구 기록이 없다" in h
    h = compare_mod.build_html("b", "e", {}, {}, [], [], {}, {}, None)
    assert "기준 · 실험 실행에 잠금 문구 기록이 없다" in h


def test_build_html_orig_from_exp_when_base_has_none(compare_mod):
    h = compare_mod.build_html("b", "e", {}, {}, [R("a")], [R("a", orig="o.webp")], {}, {}, None)
    assert 'src="e/o.webp"' in h


def test_build_html_rows_ordered_and_repeat_cells(compare_mod):
    rb = [R("same", 1), R("same", 2), R("worse", 1), R("worse", 2)]
    vb = {("same", 1): V(True), ("same", 2): V(True), ("worse", 1): V(True), ("worse", 2): V(True)}
    ve = {("same", 1): V(True), ("same", 2): V(True), ("worse", 1): V(False), ("worse", 2): V(True)}
    h = compare_mod.build_html("b", "e", {}, {}, rb, list(rb), vb, ve, None)
    assert h.index("<h3>worse</h3>") < h.index("<h3>same</h3>")
    assert "기준 r1…r2" in h
    row = h[h.index("<h3>worse</h3>"):h.index("<h3>same</h3>")]
    assert row.count("<figure>") == 6     # 원본 + 기준 2 / 빈 칸 + 실험 2
    assert row.index("기준 r2") < row.index("실험 r1")


def test_build_html_empty_runs(compare_mod):
    h = compare_mod.build_html("b", "e", {}, {}, [], [], {}, {}, None)
    assert "r1…r1" in h


# ── load_run / main ─────────────────────────────
def test_load_run_missing_results_exits(compare_mod, tmp_path):
    with pytest.raises(SystemExit) as e:
        compare_mod.load_run("nope")
    assert "nope" in str(e.value)
    (tmp_path / "results" / "runs" / "half").mkdir(parents=True)
    (tmp_path / "results" / "runs" / "half" / "meta.json").write_text("{}")
    with pytest.raises(SystemExit):
        compare_mod.load_run("half")


def test_load_run_without_meta_and_blank_lines(compare_mod, tmp_path):
    write_run(tmp_path, "r", [R("a")])
    p = tmp_path / "results" / "runs" / "r" / "results.jsonl"
    p.write_text(p.read_text() + "\n   \n", encoding="utf-8")
    meta, results = compare_mod.load_run("r")
    assert meta == {} and results == [R("a")]


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


def test_main_rater_only(compare_mod, tmp_path, capsys):
    write_run(tmp_path, "b", [R("a")], {"repeat": 1, "dataset_sha": "s"})
    write_run(tmp_path, "e", [R("a")], {"repeat": 1, "dataset_sha": "s"})
    write_review(tmp_path, "b", "kim", [{"file": "a", "repeat": 1, "reviewed": "1"}])
    write_review(tmp_path, "b", "lee", [{"file": "a", "repeat": 1, "reviewed": "1", "text_changed": "1"}])
    write_review(tmp_path, "e", "kim", [{"file": "a", "repeat": 1, "reviewed": "1"}])
    compare_mod.main(["b", "e", "--rater", "kim"])
    out = capsys.readouterr().out
    assert "| **보존 통과** (사람) | 1/1 (100%) | 1/1 (100%) |" in out
    assert "kim" in (tmp_path / "results" / "runs" / "compare-b-vs-e.html").read_text(encoding="utf-8")


def test_main_unknown_rater_exits_without_page(compare_mod, tmp_path):
    write_run(tmp_path, "b", [R("a")], {})
    write_run(tmp_path, "e", [R("a")], {})
    write_review(tmp_path, "b", "kim", [{"file": "a", "repeat": 1, "reviewed": "1"}])
    with pytest.raises(SystemExit) as e:
        compare_mod.main(["b", "e", "--rater", "<nobody>"])
    assert "kim" in str(e.value)
    assert not list((tmp_path / "results" / "runs").glob("compare-*.html"))


def test_main_rater_in_one_run_only_is_allowed(compare_mod, tmp_path, capsys):
    write_run(tmp_path, "b", [R("a")], {"repeat": 1, "dataset_sha": "s"})
    write_run(tmp_path, "e", [R("a")], {"repeat": 1, "dataset_sha": "s"})
    write_review(tmp_path, "b", "kim", [{"file": "a", "repeat": 1, "reviewed": "1"}])
    assert compare_mod.main(["b", "e", "--rater", "kim"]) == 0
    assert "실험 사람 채점 0/1" in capsys.readouterr().out


def test_main_missing_run_exits_without_page(compare_mod, tmp_path):
    write_run(tmp_path, "b", [R("a")], {})
    with pytest.raises(SystemExit):
        compare_mod.main(["b", "ghost"])
    assert not list((tmp_path / "results" / "runs").glob("compare-*.html"))
