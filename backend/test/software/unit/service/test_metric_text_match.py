from app.services.quality.metric import text_match


def test_identical_lines_in_any_order_is_full_recall():
    m = text_match(["ROLEX", "DATEJUST", "29"], ["29", "rolex", "DATEJUST"])
    assert m["recall"] == 1.0 and m["changed"] == [] and m["added"] == []


def test_garbled_line_is_reported_as_changed():
    m = text_match(["OYSTER PERPETUAL"], ["OYSTER CONTRIAL"])
    assert m["recall"] < 1.0
    assert m["changed"][0]["before"] == "oyster perpetual"
    assert m["changed"][0]["after"] == "oyster contrial"


def test_new_unrelated_line_is_added():
    m = text_match(["ROLEX"], ["ROLEX", "SWISS MADE"])
    assert m["added"] == ["swiss made"]


def test_missing_line_scores_zero():
    m = text_match(["ROLEX", "29"], ["ROLEX"])
    assert m["recall"] < 1.0
    assert any(c["before"] == "29" for c in m["changed"])


def test_empty_before_is_full_recall():
    assert text_match([], ["x"])["recall"] == 1.0
    assert text_match([], [])["added"] == []


# ── 비교는 _key(소문자+영숫자), 보고는 _norm(소문자+공백 정리) ──
import pytest  # noqa: E402

from app.services.quality.metric import _key, _norm  # noqa: E402


def test_key_and_norm():
    assert _key(" H.M ") == "hm" and _key("H-M") == "hm"
    assert _key("00 3060") == "003060"
    assert _key("17에 5433") == "17에5433"        # 한글도 글자로 남는다
    assert _key("...  --") == ""
    assert _norm("  Hello   World ") == "hello world"


@pytest.mark.parametrize("before,after", [
    (["H.M"], ["H-M"]),
    (["H.M"], ["hm"]),
    (["3060"], ["3:060"]),
    (["OFFICIAL"], ["official"]),
    (["Made in  Korea"], ["made in korea"]),
    (["00 3060"], ["00", "3060"]),              # 한 줄을 둘로 쪼개 읽음
    (["ROLEX", "00 3060"], ["00", "3060", "rolex"]),
])
def test_reading_jitter_is_full_recall(before, after):
    m = text_match(before, after)
    assert m["recall"] == 1.0, m
    assert m["changed"] == []


def test_split_line_fragments_are_not_reported_as_added():
    m = text_match(["00 3060"], ["00", "3060"])
    assert m["added"] == []


def test_really_different_char_is_below_one():
    m = text_match(["17어 5433"], ["17에 5433"])
    assert m["recall"] < 1.0
    # 보고는 공백을 유지한 _norm 표기 (키 "17어5433" 이 아님)
    assert m["changed"] == [{"before": "17어 5433", "after": "17에 5433",
                             "score": m["changed"][0]["score"]}]
    assert 0 < m["changed"][0]["score"] < 1.0


def test_changed_report_keeps_spaces_and_lowercases():
    m = text_match(["OYSTER  PERPETUAL"], ["OYSTER CONTRIAL"])
    assert m["changed"][0]["before"] == "oyster perpetual"
    assert m["changed"][0]["after"] == "oyster contrial"


def test_added_report_keeps_spaces():
    m = text_match(["ROLEX"], ["ROLEX", "Swiss   Made"])
    assert m["added"] == ["swiss made"]


def test_short_key_exact_split_is_rescued():
    """구제 기준은 길이가 아니라 "조각을 이으면 정확히 같다" — 2자도 정확히 이어지면 1.0."""
    m = text_match(["AB"], ["A", "B"])
    assert m["recall"] == 1.0


def test_three_char_key_split_is_rescued():
    m = text_match(["A-BC"], ["A", "BC"])
    assert m["recall"] == 1.0


def test_punctuation_only_lines_are_ignored_both_sides():
    m = text_match(["---", "ROLEX"], ["ROLEX", "!!!", "  "])
    assert m == {"recall": 1.0, "changed": [], "added": []}
    assert text_match(["..."], [])["recall"] == 1.0       # 비교할 원본 키가 없다


def test_missing_line_after_empty():
    m = text_match(["ROLEX"], [])
    assert m["recall"] == 0.0
    assert m["changed"] == [{"before": "rolex", "after": None, "score": 0.0}]


def test_added_substring_of_original_is_not_added():
    """결과 줄이 원본 글자의 조각이면 새 글자가 아니다 (쪼개 읽기의 나머지)."""
    m = text_match(["SAMSUNG GALAXY"], ["SAMSUNG", "GALAXY"])
    assert m["added"] == [] and m["recall"] == 1.0


def test_genuinely_new_text_still_added_with_jitter_elsewhere():
    m = text_match(["H.M"], ["H-M", "FREE SHIPPING"])
    assert m["recall"] == 1.0 and m["added"] == ["free shipping"]


def test_split_line_in_reversed_order_is_full_recall():
    """_split_match: 조각을 어떤 순서로 이어 붙여도 원본 키와 정확히 같으면 1.0."""
    assert text_match(["00 3060"], ["3060", "00"])["recall"] == 1.0


# ── _split_match: 결과 2~3 줄을 (어떤 순서로든) 이으면 정확히 같을 때만 ──
from app.services.quality.metric import SPLIT_MAX_PARTS, _fragment_of, _split_match  # noqa: E402


@pytest.mark.parametrize("before,after", [
    (["3060"], ["13060"]),                 # 숫자 하나 추가 — 부분 문자열이어도 불일치
    (["ABC"], ["XAB", "CY"]),              # 이어붙이면 포함되지만 정확히 같진 않다
    (["500"], ["1500 ml"]),
    (["00 3060"], ["3060"]),               # 조각 하나만 남음
    (["00 3060"], ["00", "30600"]),        # 조각에 글자가 붙음
    (["00 3060"], ["0", "3060"]),          # 조각에서 글자가 빠짐
])
def test_substring_is_not_rescued(before, after):
    assert text_match(before, after)["recall"] < 1.0


@pytest.mark.parametrize("after", [
    ["00", "3060"], ["3060", "00"],
    ["ab", "cd", "ef"], ["ef", "ab", "cd"], ["cd", "ef", "ab"],
])
def test_split_any_order_up_to_three_parts(after):
    before = ["00 3060"] if "00" in after else ["AB-CD-EF"]
    m = text_match(before, after)
    assert m["recall"] == 1.0 and m["changed"] == [] and m["added"] == []


def test_split_more_than_max_parts_not_rescued():
    assert SPLIT_MAX_PARTS == 3
    assert text_match(["ABCD"], ["A", "B", "C", "D"])["recall"] < 1.0


def test_split_match_duplicate_parts_are_separate_lines():
    assert _split_match("0000", ["00", "00"]) is True
    assert _split_match("0000", ["00"]) is False       # 같은 줄을 두 번 쓰지 않는다


def test_split_match_ignores_unrelated_lines():
    assert _split_match("003060", ["rolex", "3060", "swiss", "00"]) is True
    assert _split_match("003060", []) is False
    assert _split_match("003060", ["003060"]) is False   # 한 줄 그대로는 split 아님 (best 가 1.0 로 처리)


def test_split_rescue_not_reported_in_changed():
    m = text_match(["ROLEX", "00 3060"], ["rolex", "3060", "00"])
    assert m == {"recall": 1.0, "changed": [], "added": []}


# ── _fragment_of: added 제외는 원본 "한 줄"의 진부분 문자열(2자 이상)만 ──
def test_single_char_new_line_is_added():
    m = text_match(["ROLEX"], ["ROLEX", "X"])
    assert m["added"] == ["x"]


def test_fragment_across_two_original_lines_is_added():
    """두 원본 줄에 걸친 문자열은 어느 한 줄의 조각이 아니다."""
    m = text_match(["AB", "CD"], ["AB", "CD", "BC"])
    assert m["added"] == ["bc"]


def test_fragment_of_rules():
    assert _fragment_of("00", ["003060"]) is True
    assert _fragment_of("0", ["003060"]) is False          # 한 글자
    assert _fragment_of("003060", ["003060"]) is False     # 진부분 문자열 아님 (같은 줄)
    assert _fragment_of("bc", ["ab", "cd"]) is False
    assert _fragment_of("ab", []) is False


def test_partial_read_fragment_not_added_but_recall_drops():
    m = text_match(["SAMSUNG"], ["SAM"])
    assert m["added"] == [] and m["recall"] < 1.0
