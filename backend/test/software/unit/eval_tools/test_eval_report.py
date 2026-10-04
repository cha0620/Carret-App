"""eval/report.py — 순수 함수 · load_reviews · build_report."""
import csv

import pytest

REVIEW_COLS = ["file", "repeat", "mode", "reviewed", "shape_color_changed", "text_changed",
               "wear_changed", "background_issue", "framing_issue", "note"]


# ── flagged ────────────────────────────────────
@pytest.mark.parametrize("v", ["1", "y", "Y",])
def test_flagged_true(report_mod, v):
    assert report_mod.flagged(v) is True


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


def test_build_report_quality_section(report_mod):
    results = [{"file": f"{c}.webp", "repeat": 1, "mode": "generate"} for c in "abc"]
    reviews = {"r": {("a.webp", 1): {"preserved": True, "clean": True, "flags": [], "quality": 5},
                     ("b.webp", 1): {"preserved": False, "clean": False, "flags": ["added_content"], "quality": 4},
                     ("c.webp", 1): {"preserved": True, "clean": True, "flags": [], "quality": 2}}}
    text = report_mod.build_report("x", {"full": True}, results, {}, reviews)
    # 평균 3.67 · 4 이상 2/3 · 바로 쓸 수 있음(보존 + 4 이상) 1/3
    assert "| **전체** | 3 | 3.67 | 2/3 (67%) | 1/3 (33%) |" in text
    assert "5점 1 · 4점 1 · 3점 0 · 2점 1 · 1점 0" in text


TAG_COLS = REVIEW_COLS[:-1] + ["failure_tags", "note"]


def _write_tag_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TAG_COLS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def _tv(tags=(), preserved=True):
    return {"preserved": preserved, "clean": preserved, "flags": [], "tags": list(tags), "quality": None}


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


