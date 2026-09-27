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
    assert v == {"preserved": True, "clean": True, "flags": []}


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
    d = tmp_path / "reviews" / "run1"
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
    assert out["a"][("b.webp", 1)] == {"preserved": True, "clean": True, "flags": []}
    assert out["b"][("a.webp", 1)] == {"preserved": True, "clean": False, "flags": ["framing_issue"]}


def test_load_reviews_missing_dir(report_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    assert report_mod.load_reviews("nope") == {}


def test_load_reviews_blank_repeat_on_reviewed_row_crashes(report_mod, tmp_path, monkeypatch):
    """현재 동작 기록: reviewed 는 채웠는데 repeat 가 비면 ValueError 로 보고서 전체가 죽는다."""
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    _write_csv(tmp_path / "reviews" / "r" / "a.csv", [{"file": "a.webp", "repeat": "", "reviewed": "1"}])
    with pytest.raises(ValueError):
        report_mod.load_reviews("r")


def test_latest_run(report_mod, tmp_path, monkeypatch):
    monkeypatch.setattr(report_mod, "HERE", tmp_path)
    assert report_mod.latest_run() is None
    for name, has in (("20260101-0000-full", True), ("20260201-0000-analyze", True), ("20260301-0000-full", False)):
        (tmp_path / "runs" / name).mkdir(parents=True)
        if has:
            (tmp_path / "runs" / name / "results.jsonl").write_text("")
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
