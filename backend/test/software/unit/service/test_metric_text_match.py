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


# ── 비교는 _key(소문자+영숫자), 보고는 _norm(소문자+공백 정리) ──
import pytest  # noqa: E402



@pytest.mark.parametrize("before,after", [
    (["H.M"], ["H-M"]),
    (["H.M"], ["hm"]),
    (["3060"], ["3:060"]),])
def test_reading_jitter_is_full_recall(before, after):
    m = text_match(before, after)
    assert m["recall"] == 1.0, m
    assert m["changed"] == []


def test_short_key_exact_split_is_rescued():
    """구제 기준은 길이가 아니라 "조각을 이으면 정확히 같다" — 2자도 정확히 이어지면 1.0."""
    m = text_match(["AB"], ["A", "B"])
    assert m["recall"] == 1.0


def test_added_substring_of_original_is_not_added():
    """결과 줄이 원본 글자의 조각이면 새 글자가 아니다 (쪼개 읽기의 나머지)."""
    m = text_match(["SAMSUNG GALAXY"], ["SAMSUNG", "GALAXY"])
    assert m["added"] == [] and m["recall"] == 1.0


# ── _split_match: 결과 2~3 줄을 (어떤 순서로든) 이으면 정확히 같을 때만 ──
from app.services.quality.metric import SPLIT_MAX_PARTS  # noqa: E402


@pytest.mark.parametrize("before,after", [
    (["3060"], ["13060"]),                 # 숫자 하나 추가 — 부분 문자열이어도 불일치
    (["ABC"], ["XAB", "CY"]),              # 이어붙이면 포함되지만 정확히 같진 않다
    (["500"], ["1500 ml"]),])
def test_substring_is_not_rescued(before, after):
    assert text_match(before, after)["recall"] < 1.0


def test_split_more_than_max_parts_not_rescued():
    assert SPLIT_MAX_PARTS == 3
    assert text_match(["ABCD"], ["A", "B", "C", "D"])["recall"] < 1.0


def test_fragment_across_two_original_lines_is_added():
    """두 원본 줄에 걸친 문자열은 어느 한 줄의 조각이 아니다."""
    m = text_match(["AB", "CD"], ["AB", "CD", "BC"])
    assert m["added"] == ["bc"]


