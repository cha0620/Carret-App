"""eval/report.py — 순수 함수 · load_reviews · build_report."""
import csv
import math

import pytest

REVIEW_COLS = ["file", "repeat", "mode", "reviewed", "shape_color_changed", "text_changed",
               "wear_changed", "background_issue", "framing_issue", "note"]


# ── flagged ────────────────────────────────────
@pytest.mark.parametrize("v", ["1", "y", "Y", "yes", "YES", " Yes ", "x", "X", "o", "O", "true", "TRUE", " True\n", 1, True])
def test_flagged_true(report_mod, v):
    assert report_mod.flagged(v) is True


@pytest.mark.parametrize("v", ["", " ", None, "0", "n", "no", "false", "ok", "yess", "2", 0, False, "v"])
def test_flagged_false(report_mod, v):
    assert report_mod.flagged(v) is False


# ── rater_verdict ─────────────────────────────
def test_rater_verdict_not_reviewed_is_none(report_mod):
    assert report_mod.rater_verdict({"reviewed": "", "text_changed": "1"}) is None
    assert report_mod.rater_verdict({}) is None
    assert report_mod.rater_verdict({"reviewed": None}) is None


def test_rater_verdict_no_flags(report_mod):
    v = report_mod.rater_verdict({"reviewed": "y"})
    assert v == {"preserved": True, "clean": True, "flags": [], "tags": [], "quality": None}


@pytest.mark.parametrize("flag", ["shape_color_changed", "text_changed", "wear_changed"])
def test_rater_verdict_object_flag_fails_preserved(report_mod, flag):
    v = report_mod.rater_verdict({"reviewed": "1", flag: "X"})
    assert v["preserved"] is False and v["clean"] is False and v["flags"] == [flag]


@pytest.mark.parametrize("flag", ["background_issue", "framing_issue"])
def test_rater_verdict_quality_flag_only(report_mod, flag):
    v = report_mod.rater_verdict({"reviewed": "1", flag: "o"})
    assert v["preserved"] is True and v["clean"] is False and v["flags"] == [flag]


def test_rater_verdict_flag_order_and_mixed(report_mod):
    v = report_mod.rater_verdict({"reviewed": "yes", "framing_issue": "1", "text_changed": "1",
                                  "background_issue": "0", "note": "1"})
    assert v["flags"] == ["text_changed", "framing_issue"]
    assert v["preserved"] is False and v["clean"] is False


# ── majority ──────────────────────────────────
def test_majority(report_mod):
    m = report_mod.majority
    assert m([]) is None
    assert m([True]) is True
    assert m([False]) is False
    assert m([True, False]) is False          # 동점 → 실패
    assert m([True, True, False]) is True
    assert m([True, False, False]) is False
    assert m([True, True, False, False]) is False


# ── cohen_kappa ───────────────────────────────
def test_kappa_perfect(report_mod):
    assert report_mod.cohen_kappa([True, False, True, False], [True, False, True, False]) == pytest.approx(1.0)


def test_kappa_chance_level(report_mod):
    # po = 0.5, pa = pb = 0.5 → pe = 0.5 → kappa 0
    a = [True, True, False, False]
    b = [True, False, True, False]
    assert report_mod.cohen_kappa(a, b) == pytest.approx(0.0)


def test_kappa_complete_disagreement(report_mod):
    assert report_mod.cohen_kappa([True, False], [False, True]) == pytest.approx(-1.0)


def test_kappa_known_value(report_mod):
    # 10개: 둘 다 T 4, 둘 다 F 3, a만 T 2, b만 T 1 → po .7, pa .6, pb .5, pe .5 → .4
    a = [True] * 4 + [False] * 3 + [True] * 2 + [False]
    b = [True] * 4 + [False] * 3 + [False] * 2 + [True]
    assert report_mod.cohen_kappa(a, b) == pytest.approx(0.4)


def test_kappa_none_cases(report_mod):
    k = report_mod.cohen_kappa
    assert k([], []) is None
    assert k([True], [True, False]) is None
    assert k([True, True], [True, True]) is None      # 한 값만 → pe 1
    assert k([False, False], [False, False]) is None


def test_kappa_both_constant_but_different_is_zero(report_mod):
    # 한쪽은 전부 True, 다른 쪽은 전부 False → pe 0, po 0 → 0 (None 아님)
    assert report_mod.cohen_kappa([True, True], [False, False]) == pytest.approx(0.0)


# ── auc ───────────────────────────────────────
def test_auc(report_mod):
    a = report_mod.auc
    assert a([0.9, 0.8, 0.2, 0.1], [True, True, False, False]) == 1.0
    assert a([0.1, 0.2, 0.8, 0.9], [True, True, False, False]) == 0.0
    assert a([0.5, 0.5, 0.5], [True, False, True]) == 0.5
    assert a([0.9, 0.5, 0.5], [True, True, False]) == 0.75
    assert a([0.1, 0.2], [True, True]) is None
    assert a([0.1, 0.2], [False, False]) is None
    assert a([], []) is None


# ── pearson ───────────────────────────────────
def test_pearson(report_mod):
    p = report_mod.pearson
    assert p([1, 2], [1, 2]) is None
    assert p([], []) is None
    assert p([1, 2, 3], [2, 4, 6]) == pytest.approx(1.0)
    assert p([1, 2, 3], [3, 2, 1]) == pytest.approx(-1.0)
    assert p([1, 1, 1], [1, 2, 3]) is None
    assert p([1, 2, 3], [5, 5, 5]) is None
    assert p([1, 2, 3, 4], [1.0, 0.0, 0.0, 1.0]) == pytest.approx(0.0)


# ── metric_value ──────────────────────────────
def test_metric_value(report_mod):
    mv = report_mod.metric_value
    row = {"judge": {"fidelity": 4, "trust": None}, "gate_passed": True, "visual_similarity": 0.83,
           "item_similarity": "0.5", "verify_failed": False}
    assert mv(row, "judge.fidelity") == 4.0 and isinstance(mv(row, "judge.fidelity"), float)
    assert mv(row, "judge.trust") is None
    assert mv(row, "judge.realism") is None
    assert mv(row, "gate_passed") == 1.0
    assert mv(row, "verify_failed") == 0.0
    assert mv(row, "visual_similarity") == pytest.approx(0.83)
    assert mv(row, "item_similarity") is None          # 문자열은 값으로 안 친다
    assert mv(row, "missing") is None
    assert mv({"judge": None}, "judge.fidelity") is None
    assert mv({"judge": 3}, "judge.fidelity") is None
    assert mv({}, "judge.fidelity") is None


# ── pct / fmt ─────────────────────────────────
def test_pct_fmt(report_mod):
    assert report_mod.pct(0, 0) == "—"
    assert report_mod.pct(3, 4) == "3/4 (75%)"
    assert report_mod.pct(0, 5) == "0/5 (0%)"
    assert report_mod.pct(2, 3) == "2/3 (67%)"
    assert report_mod.fmt(None) == "—"
    assert report_mod.fmt(0.5) == "0.50"
    assert report_mod.fmt(1 / 3, 3) == "0.333"
    assert report_mod.fmt(0) == "0.00"


# ── load_reviews ──────────────────────────────
def _write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=REVIEW_COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_load_reviews(report_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    d = tmp_path / "results" / "reviews" / "run1"
    _write_csv(d / "TEMPLATE.csv", [{"file": "a.webp", "repeat": "1", "reviewed": "1"}])
    _write_csv(d / "a.csv", [
        {"file": "a.webp", "repeat": "1", "reviewed": "1", "text_changed": "x"},
        {"file": "a.webp", "repeat": "2", "reviewed": ""},              # 안 봄 → 빠짐
        {"file": "b.webp", "repeat": "1", "reviewed": "Y"},
    ])
    _write_csv(d / "b.csv", [{"file": "a.webp", "repeat": "1", "reviewed": "1", "framing_issue": "1"}])
    _write_csv(d / "c.csv", [{"file": "a.webp", "repeat": "1", "reviewed": ""}])   # 전부 빈 → 평가자 빠짐

    out = report_mod.load_reviews("run1")
    assert set(out) == {"a", "b"}
    assert set(out["a"]) == {("a.webp", 1), ("b.webp", 1)}
    assert all(isinstance(k[1], int) for k in out["a"])
    assert out["a"][("a.webp", 1)]["preserved"] is False
    assert out["a"][("b.webp", 1)] == {"preserved": True, "clean": True, "flags": [], "tags": [], "quality": None}
    assert out["b"][("a.webp", 1)] == {"preserved": True, "clean": False, "flags": ["framing_issue"], "tags": [], "quality": None}


def test_load_reviews_missing_dir(report_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    assert report_mod.load_reviews("nope") == {}


def test_load_reviews_skips_broken_repeat_rows(report_mod, tmp_path, monkeypatch):
    """reviewed 는 채웠는데 repeat 가 비거나 이상한 줄은 건너뛴다 (보고서 전체가 죽지 않게)."""
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    _write_csv(tmp_path / "results" / "reviews" / "r" / "a.csv", [
        {"file": "a.webp", "repeat": "", "reviewed": "1"},
        {"file": "a.webp", "repeat": "x", "reviewed": "1"},
        {"file": "a.webp", "repeat": "1.5", "reviewed": "1"},
        {"file": "b.webp", "repeat": "2", "reviewed": "1"},
    ])
    assert set(report_mod.load_reviews("r")["a"]) == {("b.webp", 2)}
    _write_csv(tmp_path / "results" / "reviews" / "s" / "a.csv", [{"file": "a.webp", "repeat": "", "reviewed": "1"}])
    assert report_mod.load_reviews("s") == {}                        # 전부 깨졌으면 평가자 빠짐


def test_load_reviews_missing_file_column_skipped(report_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    p = tmp_path / "results" / "reviews" / "r" / "a.csv"
    p.parent.mkdir(parents=True)
    p.write_text("repeat,reviewed\n1,y\n", encoding="utf-8")
    assert report_mod.load_reviews("r") == {}


def test_load_reviews_accepts_excel_bom(report_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    p = tmp_path / "results" / "reviews" / "r" / "a.csv"
    p.parent.mkdir(parents=True)
    p.write_text("file,repeat,reviewed,failure_tags\nbag.webp,1,y,tag_lost\n", encoding="utf-8-sig")
    assert p.read_bytes().startswith(b"\xef\xbb\xbf")
    assert report_mod.load_reviews("r")["a"][("bag.webp", 1)]["tags"] == ["tag_lost"]


def test_latest_run(report_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    assert report_mod.latest_run() is None
    for name, has in (("20260101-0000-full", True), ("20260201-0000-analyze", True), ("20260301-0000-full", False)):
        (tmp_path / "results" / "runs" / name).mkdir(parents=True)
        if has:
            (tmp_path / "results" / "runs" / name / "results.jsonl").write_text("")
    assert report_mod.latest_run() == "20260201-0000-analyze"


# ── build_report ──────────────────────────────
GT = {
    "doc.webp": {"file": "doc.webp", "photo_type": "document", "wear_level": "none", "text_level": "heavy"},
    "bag.webp": {"file": "bag.webp", "photo_type": "product", "wear_level": "light", "text_level": "none"},
    "box.webp": {"file": "box.webp", "photo_type": "inside_view", "wear_level": "none", "text_level": "light"},
}


def _analyze_results():
    return [
        {"file": "doc.webp", "repeat": 1, "photo_type": "document", "wear_level": "none", "text_level": "heavy"},
        {"file": "bag.webp", "repeat": 1, "photo_type": "document", "wear_level": "light", "text_level": "none"},
        {"file": "box.webp", "repeat": 1, "error": "RuntimeError: boom"},
    ]


def test_build_report_analyze_only(report_mod):
    text = report_mod.build_report("r-analyze", {"full": False, "repeat": 1}, _analyze_results(), GT, {})
    assert "analyze 만" in text
    assert "사진 3장" in text and "실행 3번" in text and "오류 1번" in text
    assert "## 1. 분석 정확도" in text
    assert "## 2." not in text and "## 3." not in text
    # 오류 행(box)은 분모에서 빠진다 → 2개 중
    assert "| photo_type | 1/2 (50%) | bag.webp (product → document) |" in text
    assert "| wear_level | 2/2 (100%) | — |" in text
    # 혼동표: 정답 product 행에 document 1
    assert "| product | 1 | 0 | 0 | 0 |" in text
    assert "| document | 1 | 0 | 0 | 0 |" in text
    assert "| inside_view | 0 | 0 | 0 | 0 |" in text


def test_build_report_accuracy_counts_repeat1_only(report_mod):
    results = [
        {"file": "doc.webp", "repeat": 1, "photo_type": "document", "wear_level": "none", "text_level": "heavy", "mode": "original"},
        {"file": "doc.webp", "repeat": 2, "photo_type": "product", "wear_level": "heavy", "text_level": "none", "mode": "generate"},
    ]
    text = report_mod.build_report("r", {"full": False, "repeat": 2}, results, GT, {})
    assert "| photo_type | 1/1 (100%) | — |" in text
    assert "| text_level | 1/1 (100%) | — |" in text


def test_build_report_unknown_photo_type_goes_to_other(report_mod):
    results = [{"file": "bag.webp", "repeat": 1, "photo_type": "weird"}]
    text = report_mod.build_report("r", {"full": False}, results, GT, {})
    assert "| product | 0 | 0 | 0 | 1 |" in text


def test_build_report_no_gt_skips_confusion(report_mod):
    results = [{"file": "zzz.webp", "repeat": 1, "photo_type": "document"}]
    text = report_mod.build_report("r", {"full": False}, results, GT, {})
    assert "| photo_type | — | — |" in text
    assert "혼동표" not in text


def _full_results():
    return [
        {"file": "doc.webp", "repeat": 1, "photo_type": "document", "wear_level": "none", "text_level": "heavy",
         "mode": "composite", "composite_reason": "document"},
        {"file": "doc.webp", "repeat": 2, "photo_type": "document", "wear_level": "none", "text_level": "heavy",
         "mode": "composite", "composite_reason": "document"},
        {"file": "bag.webp", "repeat": 1, "photo_type": "product", "wear_level": "light", "text_level": "none",
         "mode": "generate", "judge": {"fidelity": 5, "trust": 5}, "gate_passed": True, "visual_similarity": 0.9},
        {"file": "bag.webp", "repeat": 2, "photo_type": "product", "wear_level": "light", "text_level": "none",
         "mode": "generate", "judge": {"fidelity": 2, "trust": 3}, "gate_passed": False, "visual_similarity": 0.4},
        {"file": "box.webp", "repeat": 1, "error": "RuntimeError: boom"},
        {"file": "box.webp", "repeat": 2, "photo_type": "inside_view", "mode": "original"},
    ]


def test_build_report_full_without_reviews(report_mod):
    text = report_mod.build_report("r-full", {"full": True, "repeat": 2, "preset": "studio_white"}, _full_results(), GT, {})
    assert "전체 파이프라인" in text and "프리셋 studio_white" in text
    assert "## 2. 경로 분포" in text
    assert "| document | 0 | 2 | 0 | 0 |" in text
    assert "| product | 2 | 0 | 0 | 0 |" in text
    assert "| inside_view | 0 | 0 | 1 | 0 |" in text       # 오류 행 제외
    assert "생성하지 않은 이유: document 2" in text
    assert "채점표가 아직 없다" in text and "reviews/r-full/TEMPLATE.csv" in text
    assert "## 4." not in text and "## 5." not in text


def _v(preserved, clean, flags=()):
    return {"preserved": preserved, "clean": clean, "flags": list(flags)}


def test_build_report_full_with_two_raters(report_mod):
    reviews = {
        "alice": {
            ("doc.webp", 1): _v(True, True),
            ("doc.webp", 2): _v(True, False, ["background_issue"]),
            ("bag.webp", 1): _v(True, True),
            ("bag.webp", 2): _v(False, False, ["text_changed"]),
            ("box.webp", 1): _v(False, False, ["wear_changed"]),   # 오류 행 → 무시돼야 함
        },
        "bob": {
            ("doc.webp", 1): _v(True, True),
            ("bag.webp", 1): _v(True, True),
            ("bag.webp", 2): _v(True, True),                         # alice 와 불일치 → 동점 → 실패
            ("box.webp", 2): _v(True, True),
        },
    }
    text = report_mod.build_report("r-full", {"full": True, "repeat": 2}, _full_results(), GT, reviews)
    assert "평가자 2명 (alice, bob) · 채점된 실행 5/5" in text
    # 경로별
    assert "| generate | 1/2 (50%) | 1/2 (50%) |" in text
    assert "| composite | 2/2 (100%) | 1/2 (50%) |" in text
    assert "| original | 1/1 (100%) | 1/1 (100%) |" in text
    assert "| **전체** | 4/5 (80%) | 3/5 (60%) |" in text
    # 종류별
    assert "| document | 2/2 (100%) |" in text
    assert "| product | 1/2 (50%) |" in text
    assert "| inside_view | 1/1 (100%) |" in text
    # 물건 기준 (repeat 2): 3개 물건 전부 한 번 이상 통과, 매번 통과는 doc·box
    assert "물건 기준 (repeat 2): 한 번이라도 통과 3/3 (100%) · 매번 통과 2/3 (67%)" in text
    # 실패 유형 — 오류 행(box r1)의 wear_changed 는 빠진다
    assert "실패 유형" in text
    assert "background_issue 1" in text and "text_changed 1" in text
    assert "wear_changed" not in text
    # kappa: 공통 키 = doc1, bag1, bag2 → 일치 2/3
    assert "## 4. 평가자 일치도" in text
    assert "| alice · bob | 3 | 2/3 (67%) |" in text
    # 5: 생성본 2개만
    assert "## 5. 자동 지표" in text and "생성본 2개" in text
    assert "| judge.fidelity | 2 | 1.00 | — |" in text          # n<3 → 상관 없음
    assert "| gate_passed | 2 | 1.00 | — |" in text
    assert "| item_similarity | 0 | — | — |" in text


def test_build_report_single_rater_no_kappa_and_repeat1_no_item_line(report_mod):
    results = [r for r in _full_results() if r["repeat"] == 1]
    reviews = {"alice": {("bag.webp", 1): _v(True, True), ("doc.webp", 1): _v(True, True)}}
    text = report_mod.build_report("r", {"full": True, "repeat": 1}, results, GT, reviews)
    assert "평가자 1명 (alice) · 채점된 실행 2/2" in text
    assert "## 4." not in text
    assert "물건 기준" not in text
    assert "실패 유형" not in text
    # 생성본 1개, 한쪽 라벨뿐 → AUC 없음
    assert "생성본 1개" in text
    assert "| judge.fidelity | 1 | — | — |" in text
    # 채점 안 된 경로는 — 로
    assert "| original | — | — |" in text


def test_build_report_auc_and_correlation_with_three_generated(report_mod):
    results = [
        {"file": "bag.webp", "repeat": i, "mode": "generate", "photo_type": "product",
         "judge": {"fidelity": f}, "visual_similarity": vs}
        for i, (f, vs) in enumerate([(5, 0.2), (4, 0.9), (1, 0.8)], start=1)
    ]
    reviews = {"alice": {("bag.webp", 1): _v(True, True), ("bag.webp", 2): _v(True, True),
                         ("bag.webp", 3): _v(False, False, ["shape_color_changed"])}}
    text = report_mod.build_report("r", {"full": True, "repeat": 3}, results, GT, reviews)
    line = next(l for l in text.splitlines() if l.startswith("| judge.fidelity |"))
    cells = [c.strip() for c in line.strip("|").split("|")]
    assert cells[1] == "3" and cells[2] == "1.00"
    assert float(cells[3]) > 0.8
    vs_line = next(l for l in text.splitlines() if l.startswith("| visual_similarity |"))
    assert vs_line.split("|")[3].strip() == "0.50"     # 0.2,0.9 vs 0.8 → (0 + 1)/2
    assert "물건 기준 (repeat 3): 한 번이라도 통과 1/1 (100%) · 매번 통과 0/1 (0%)" in text


def test_build_report_empty_results(report_mod):
    text = report_mod.build_report("r", {}, [], GT, {})
    assert "사진 0장" in text and "실행 0번" in text
    assert "| photo_type | — | — |" in text
    assert "## 2." not in text


def test_build_report_kappa_includes_error_rows(report_mod):
    """현재 동작 기록: 3절(판정)은 오류 행을 빼지만 4절(kappa)은 reviews 원본 키를 그대로 써서
    오류 난 실행(결과 이미지 없음)에 대한 채점도 일치도에 들어간다."""
    results = [{"file": "bag.webp", "repeat": 1, "mode": "generate"},
               {"file": "box.webp", "repeat": 1, "error": "RuntimeError: boom"}]
    reviews = {"a": {("bag.webp", 1): _v(True, True), ("box.webp", 1): _v(True, True)},
               "b": {("bag.webp", 1): _v(True, True), ("box.webp", 1): _v(False, False, ["text_changed"])}}
    text = report_mod.build_report("r", {"full": True}, results, GT, reviews)
    assert "채점된 실행 1/1" in text
    assert "| a · b | 2 | 1/2 (50%) |" in text


# ── 10-01: 없던 것 생김 · 상품 사진 품질 ──────────
def test_added_content_fails_preservation(report_mod):
    v = report_mod.rater_verdict({"reviewed": "y", "added_content": "1"})
    assert v["preserved"] is False and v["clean"] is False and v["flags"] == ["added_content"]


@pytest.mark.parametrize("raw,expected", [("5", 5), (" 3 ", 3), ("1", 1), ("", None), ("0", None),
                                          ("6", None), ("4.5", None), ("x", None), (None, None)])
def test_rater_verdict_quality_parsed(report_mod, raw, expected):
    assert report_mod.rater_verdict({"reviewed": "y", "photo_quality": raw})["quality"] == expected


def test_build_report_quality_section(report_mod):
    results = [{"file": f"{c}.webp", "repeat": 1, "mode": "generate"} for c in "abc"]
    reviews = {"r": {("a.webp", 1): {"preserved": True, "clean": True, "flags": [], "quality": 5},
                     ("b.webp", 1): {"preserved": False, "clean": False, "flags": ["added_content"], "quality": 4},
                     ("c.webp", 1): {"preserved": True, "clean": True, "flags": [], "quality": 2}}}
    text = report_mod.build_report("x", {"full": True}, results, {}, reviews)
    # 평균 3.67 · 4 이상 2/3 · 바로 쓸 수 있음(보존 + 4 이상) 1/3
    assert "| **전체** | 3 | 3.67 | 2/3 (67%) | 1/3 (33%) |" in text
    assert "5점 1 · 4점 1 · 3점 0 · 2점 1 · 1점 0" in text


def test_build_report_no_quality_section_for_old_sheets(report_mod):
    results = [{"file": "a.webp", "repeat": 1, "mode": "generate"}]
    reviews = {"r": {("a.webp", 1): {"preserved": True, "clean": True, "flags": []}}}   # 옛 형식 (quality 없음)
    assert "상품 사진 품질" not in report_mod.build_report("x", {"full": True}, results, {}, reviews)


def test_build_report_shows_conditions_only_when_recorded(report_mod):
    meta = {"full": False, "gen_model": "fal-ai/m", "gen_steps": 8, "vlm_model": "v", "lock_sha": "abc",
            "commit": "1234567", "app_dirty": True, "app_diff_sha": "ffff", "note": "신발만"}
    text = report_mod.build_report("x", meta, [], {}, {})
    assert "조건: 생성 fal-ai/m · 8스텝 · VLM v · 잠금 abc · 코드 1234567 (커밋 안 된 변경 ffff) · 메모: 신발만" in text
    assert "조건:" not in report_mod.build_report("x", {"full": False}, [], {}, {})


# ── 10-01: 사진별 경로 ──────────────────────
@pytest.mark.parametrize("row,expected", [
    ({"error": "boom"}, "오류: boom"),
    ({"photo_type": "inside_view", "mode": "original", "composite_reason": "inside_view"},
     "analyze(inside_view/None/None) → 원본 그대로"),
    ({"photo_type": "document", "text_level": "dense", "mode": "composite", "composite_reason": "document"},
     "analyze(document/None/dense) → 배경 교체 [document]"),
    ({"photo_type": "product", "wear_level": "heavy", "mode": "original", "composite_reason": "wear_heavy"},
     "analyze(product/heavy/None) → 오리기 실패 → 원본 그대로 [wear_heavy]"),
    ({"photo_type": "product", "text_level": "simple", "mode": "composite", "composite_reason": "text_heavy"},
     "analyze(product/None/simple) → 글자 읽기(12줄 이상) → 배경 교체 [text_heavy]"),
    ({"photo_type": "product", "text_level": "dense", "mode": "generate", "composite_reason": "text_dense",
      "gen_attempts": 1}, "analyze(product/None/dense) → 배경 교체 실패 → 생성 ×1"),
    ({"photo_type": "product", "text_level": "simple", "mode": "generate", "gen_attempts": 2, "gate_passed": True},
     "analyze(product/None/simple) → 글자 읽기 → 생성 ×2 → 게이트 통과"),
    ({"photo_type": "product", "text_level": "none", "mode": "composite", "composite_reason": "gate_failed",
      "gen_attempts": 2}, "analyze(product/None/none) → 생성 ×2 → 게이트 실패 → 재생성 → 실패 → 배경 교체 [gate_failed]"),
    ({"photo_type": "product", "text_level": "none", "mode": "generate", "gen_attempts": 1, "gate_passed": False},
     "analyze(product/None/none) → 생성 ×1 → 게이트 실패 → 생성본 그대로 (후퇴 꺼짐)"),
])
def test_route_trace(report_mod, row, expected):
    assert report_mod.route_trace(row) == expected


def test_build_report_lists_routes(report_mod):
    results = [{"file": "a.webp", "repeat": 1, "photo_type": "product", "text_level": "none", "mode": "generate",
                "gen_attempts": 1, "gate_passed": True},
               {"file": "b.webp", "repeat": 1, "error": "x"}]
    text = report_mod.build_report("x", {"full": True}, results, {}, {})
    assert "| analyze(product/None/none) → 생성 ×1 → 게이트 통과 | 1 |" in text
    assert "| b.webp | 1 | 오류: x |" in text


# ── 10-02: 실패 원인 태그 ──────────────────────
@pytest.mark.parametrize("raw,expected", [
    (None, []), ("", []), (" ", []), (";", []), (";;", []),
    ("detail_lost", ["detail_lost"]),
    ("tag_lost;detail_lost", ["detail_lost", "tag_lost"]),               # 정렬
    (" tag_lost ; detail_lost ;", ["detail_lost", "tag_lost"]),          # 공백·빈 조각
    ("color_changed;color_changed", ["color_changed"]),                  # 중복
    ("mystery", ["mystery"]),                                             # 모르는 태그도 그대로 (report 는 거르지 않는다)
    ("tag_lost,detail_lost", ["detail_lost", "tag_lost"]),               # "," 도 구분자
    (" tag_lost , detail_lost ; color_changed,", ["color_changed", "detail_lost", "tag_lost"]),
    (",;,", []),
])
def test_rater_verdict_tags(report_mod, raw, expected):
    assert report_mod.rater_verdict({"reviewed": "y", "failure_tags": raw})["tags"] == expected


def test_rater_verdict_tags_do_not_affect_preserved(report_mod):
    v = report_mod.rater_verdict({"reviewed": "y", "failure_tags": "color_changed"})
    assert v["preserved"] is True and v["clean"] is True and v["flags"] == []


def test_rater_verdict_tags_missing_column(report_mod):
    assert report_mod.rater_verdict({"reviewed": "y"})["tags"] == []


TAG_COLS = REVIEW_COLS[:-1] + ["failure_tags", "note"]


def _write_tag_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TAG_COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def test_load_reviews_reads_tags(report_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    _write_tag_csv(tmp_path / "results" / "reviews" / "r" / "a.csv", [
        {"file": "a.webp", "repeat": "1", "reviewed": "y", "failure_tags": "tag_lost;detail_lost"},
        {"file": "a.webp", "repeat": "2", "reviewed": "", "failure_tags": "detail_lost"},   # 안 봄 → 빠짐
    ])
    out = report_mod.load_reviews("r")
    assert out == {"a": {("a.webp", 1): {"preserved": True, "clean": True, "flags": [],
                                         "tags": ["detail_lost", "tag_lost"], "quality": None}}}


def _tv(tags=(), preserved=True):
    return {"preserved": preserved, "clean": preserved, "flags": [], "tags": list(tags), "quality": None}


def test_build_report_tag_line_counts_each_run_once(report_mod):
    results = [{"file": f"{c}.webp", "repeat": 1, "mode": "generate"} for c in "abc"]
    reviews = {"x": {("a.webp", 1): _tv(["color_changed", "detail_lost"]), ("b.webp", 1): _tv(["color_changed"]),
                     ("c.webp", 1): _tv()},
               "y": {("a.webp", 1): _tv(["color_changed"]), ("b.webp", 1): _tv(["tag_lost"])}}
    text = report_mod.build_report("r", {"full": True}, results, {}, reviews)
    # color_changed: a(두 명) + b = 2 (a 를 두 번 세지 않는다)
    assert "실패 원인 태그 (한 명이라도 붙인 실행 수): color_changed 2, detail_lost 1, tag_lost 1" in text
    assert "태그는 있는데 물건 표시가 없어 보존 통과로 센 실행 2개" in text   # a · b 모두 preserved


def test_build_report_odd_tag_line_only_when_tag_but_preserved(report_mod):
    results = [{"file": f"{c}.webp", "repeat": 1, "mode": "generate"} for c in "ab"]
    flagged = {"preserved": False, "clean": False, "flags": ["shape_color_changed"], "tags": ["color_changed"],
               "quality": None}
    text = report_mod.build_report("r", {"full": True}, results, {}, {"x": {("a.webp", 1): flagged,
                                                                          ("b.webp", 1): _tv()}})
    assert "실패 원인 태그 (한 명이라도 붙인 실행 수): color_changed 1" in text
    assert "태그는 있는데 물건 표시가 없어" not in text
    # 평가자 둘 — 한 명은 표시 없이 태그만, 다른 한 명은 표시. 다수결 동점 → 실패 → 보존 통과 아님 → 줄 없음
    text = report_mod.build_report("r", {"full": True}, results[:1], {},
                                   {"x": {("a.webp", 1): _tv(["tag_lost"])}, "y": {("a.webp", 1): flagged}})
    assert "태그는 있는데 물건 표시가 없어" not in text
    # 셋 중 둘이 표시 없음 → 다수결 보존 통과 + 태그 있음 → 1개
    text = report_mod.build_report("r", {"full": True}, results[:1], {},
                                   {"x": {("a.webp", 1): _tv(["tag_lost"])}, "y": {("a.webp", 1): _tv()},
                                    "z": {("a.webp", 1): flagged}})
    assert "태그는 있는데 물건 표시가 없어 보존 통과로 센 실행 1개 — 채점표 확인" in text


def test_build_report_no_tag_line_without_tags(report_mod):
    results = [{"file": "a.webp", "repeat": 1, "mode": "generate"}]
    text = report_mod.build_report("r", {"full": True}, results, {}, {"x": {("a.webp", 1): _tv()}})
    assert "실패 원인 태그" not in text


def test_build_report_old_verdict_without_tags_key(report_mod):
    """옛 형식(tags 키 없음)도 터지지 않는다."""
    results = [{"file": "a.webp", "repeat": 1, "mode": "generate"}]
    reviews = {"x": {("a.webp", 1): {"preserved": True, "clean": True, "flags": []}},
               "y": {("a.webp", 1): _tv(["tag_lost"])}}
    text = report_mod.build_report("r", {"full": True}, results, {}, reviews)
    assert "실패 원인 태그 (한 명이라도 붙인 실행 수): tag_lost 1" in text


def test_build_report_tags_on_error_rows_ignored(report_mod):
    """오류 난 실행에 붙은 태그는 verdict 에 안 들어간다 (ok 줄만 본다)."""
    results = [{"file": "a.webp", "repeat": 1, "error": "boom"}, {"file": "b.webp", "repeat": 1, "mode": "generate"}]
    reviews = {"x": {("a.webp", 1): _tv(["tag_lost"]), ("b.webp", 1): _tv()}}
    assert "실패 원인 태그" not in report_mod.build_report("r", {"full": True}, results, {}, reviews)


# ── tag_summary ──
def test_tag_summary_empty(report_mod):
    msg = "실패 원인 태그가 붙은 채점이 없다"
    assert report_mod.tag_summary({}) == msg
    assert report_mod.tag_summary({"r1": {}}) == msg
    assert report_mod.tag_summary({"r1": {"a": {("x.webp", 1): _tv()}}}) == msg


def test_tag_summary_multiple_raters_same_row_once(report_mod):
    reviews = {"a": {("bag.webp", 1): _tv(["color_changed"])},
               "b": {("bag.webp", 1): _tv(["color_changed", "tag_lost"])}}
    out = report_mod.tag_summary({"20261001-pilot": reviews})
    lines = out.splitlines()
    assert lines[:2] == ["| 태그 | 실행 수 | 사진 수 | 예 |", "|---|---|---|---|"]
    assert "| color_changed | 1 | 1 | 20261001-pilot/bag.webp r1 |" in lines
    assert "| tag_lost | 1 | 1 | 20261001-pilot/bag.webp r1 |" in lines


def test_tag_summary_counts_runs_and_photos_across_runs(report_mod):
    by_run = {
        "20261001-b": {"x": {("bag.webp", 1): _tv(["detail_lost"]), ("bag.webp", 2): _tv(["detail_lost"]),
                             ("shoe.webp", 1): _tv(["detail_lost", "tag_lost"])}},
        "20261001-a": {"y": {("bag.webp", 1): _tv(["detail_lost"])}},
    }
    lines = report_mod.tag_summary(by_run).splitlines()
    # 실행 수 4 (b 의 3줄 + a 의 1줄), 사진 수는 파일 이름 기준 2 (bag 은 실행이 달라도 한 장)
    # 예는 run_id 정렬 순 → a 먼저
    assert lines[2] == "| detail_lost | 4 | 2 | 20261001-a/bag.webp r1, 20261001-b/bag.webp r1, 20261001-b/bag.webp r2, 20261001-b/shoe.webp r1 |"
    assert lines[3] == "| tag_lost | 1 | 1 | 20261001-b/shoe.webp r1 |"          # 많은 순


def test_tag_summary_truncates_examples_after_four(report_mod):
    rows = {(f"p{i}.webp", 1): _tv(["color_changed"]) for i in range(6)}
    out = report_mod.tag_summary({"run-x": {"a": rows}})
    line = [ln for ln in out.splitlines() if ln.startswith("| color_changed")][0]
    assert line == "| color_changed | 6 | 6 | run-x/p0.webp r1, run-x/p1.webp r1, run-x/p2.webp r1, run-x/p3.webp r1 … |"


def test_tag_summary_exactly_four_no_ellipsis(report_mod):
    rows = {(f"p{i}.webp", 1): _tv(["color_changed"]) for i in range(4)}
    out = report_mod.tag_summary({"run-x": {"a": rows}})
    assert "…" not in out and "run-x/p3.webp r1 |" in out


def test_tag_summary_nested_file_and_same_name_in_different_dirs(report_mod):
    """파일 이름에 '/' 가 있어도 사진 수는 run_id 뒤 전체 경로로 센다."""
    rows = {("sub/bag.webp", 3): _tv(["tag_lost"]), ("other/bag.webp", 1): _tv(["tag_lost"])}
    out = report_mod.tag_summary({"pilot": {"a": rows}})
    assert "| tag_lost | 2 | 2 | pilot/other/bag.webp r1, pilot/sub/bag.webp r3 |" in out


def test_tag_summary_old_verdict_without_tags_key(report_mod):
    reviews = {"a": {("bag.webp", 1): {"preserved": True, "clean": True, "flags": []}}}
    assert report_mod.tag_summary({"r": reviews}) == "실패 원인 태그가 붙은 채점이 없다"


# ── main --tags ──
def test_main_tags_mode(report_mod, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    _write_tag_csv(tmp_path / "results" / "reviews" / "20261001-a" / "x.csv",
                   [{"file": "bag.webp", "repeat": "1", "reviewed": "y", "failure_tags": "tag_lost"}])
    _write_tag_csv(tmp_path / "results" / "reviews" / "20261001-a" / "TEMPLATE.csv",
                   [{"file": "bag.webp", "repeat": "1", "reviewed": "y", "failure_tags": "color_changed"}])
    _write_tag_csv(tmp_path / "results" / "reviews" / "20261002-b" / "y.csv",
                   [{"file": "bag.webp", "repeat": "1", "reviewed": "y", "failure_tags": "tag_lost"}])
    (tmp_path / "results" / "reviews" / "stray.csv").write_text("x", encoding="utf-8")   # 디렉터리 아닌 건 무시
    monkeypatch.setattr(report_mod.sys, "argv", ["report.py", "--tags"])
    assert report_mod.main() == 0
    out = capsys.readouterr().out
    assert "| tag_lost | 2 | 1 | 20261001-a/bag.webp r1, 20261002-b/bag.webp r1 |" in out
    assert "color_changed" not in out                                    # TEMPLATE 은 뺀다
    assert not (tmp_path / "results" / "runs").exists()                              # runs/ 는 안 건드린다


def test_main_tags_mode_without_reviews_dir(report_mod, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    monkeypatch.setattr(report_mod.sys, "argv", ["report.py", "--tags"])
    assert report_mod.main() == 0
    assert "실패 원인 태그가 붙은 채점이 없다" in capsys.readouterr().out


def test_main_tags_must_be_first_arg(report_mod, tmp_path, monkeypatch):
    """--tags 가 첫 인자가 아니면 run_id 로 읽힌다 (현재 동작 기록)."""
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    monkeypatch.setattr(report_mod.sys, "argv", ["report.py", "some-run", "--tags"])
    with pytest.raises(FileNotFoundError):
        report_mod.main()
