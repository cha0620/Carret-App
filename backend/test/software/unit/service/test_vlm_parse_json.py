"""app.core.vlm.parse_json — VLM 응답 JSON 읽기. 잘못된 \\u(16진수 4자리 아님)만 고쳐 다시 읽는다."""
import json

import pytest

from app.core.vlm import parse_json


def test_valid_json():
    assert parse_json('{"a": [1, 2]}') == {"a": [1, 2]}


def test_valid_unicode_escape_kept():
    assert parse_json(r'{"t": "\uD55C\u0041"}') == {"t": "한A"}


def test_invalid_u_escape_repaired():
    # 회귀: 사진 속 글자 옮기다 "\u" 뒤에 16진수가 아닌 게 와서 묶기가 0개로 빠졌다
    assert parse_json(r'{"t": "C:\users \u0041"}') == {"t": r"C:\users A"}


def test_still_broken_raises():
    with pytest.raises(json.JSONDecodeError):
        parse_json(r'{"t": "\uZZ" ')
