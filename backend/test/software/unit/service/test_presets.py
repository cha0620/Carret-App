"""app.prompts.presets.get_preset - Langfuse fallback 경로에서 계약 유지 검증."""
import pytest

import app.core.tracing as tracing
import app.prompts.presets as presets_mod
from app.prompts.presets import (PRESETS, SECONDHAND_LOCK, get_preset,
                                 with_secondhand_lock)


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


@pytest.mark.parametrize("key", list(PRESETS.keys()))
def test_get_preset_returns_expected_keys_and_secondhand_lock(key):
    preset = get_preset(key)
    assert set(preset.keys()) == {"name", "prompt", "bg_color"}
    assert preset["name"] == PRESETS[key]["name"]
    assert preset["bg_color"] == PRESETS[key]["bg_color"]
    assert SECONDHAND_LOCK in preset["prompt"]


def test_get_preset_prompt_matches_fallback_verbatim_when_langfuse_disabled():
    # Langfuse 비활성 → fallback 배경 묘사 + " " + 잠금 (옛 f-string 출력과 동일)
    for key in PRESETS:
        preset = get_preset(key)
        assert preset["prompt"] == PRESETS[key]["fallback_prompt"] + " " + SECONDHAND_LOCK


def test_get_preset_unknown_key_raises_keyerror():
    with pytest.raises(KeyError):
        get_preset("not_a_real_preset_key")


@pytest.mark.parametrize("key", list(PRESETS.keys()))
def test_fallback_prompt_does_not_contain_secondhand_lock(key):
    assert SECONDHAND_LOCK not in PRESETS[key]["fallback_prompt"]


def test_with_secondhand_lock_appends_exactly_once():
    out = with_secondhand_lock("white background.")
    assert out == "white background. " + SECONDHAND_LOCK
    assert out.count(SECONDHAND_LOCK) == 1


def test_with_secondhand_lock_is_idempotent():
    once = with_secondhand_lock("white background.")
    assert with_secondhand_lock(once) == once
    assert with_secondhand_lock(with_secondhand_lock(once)) == once


def test_with_secondhand_lock_leaves_text_ending_with_lock_unchanged():
    # Langfuse v1 프리셋처럼 " LOCK" 으로 끝나는 경우 그대로
    v1 = f"product photo. {SECONDHAND_LOCK}"
    assert with_secondhand_lock(v1) == v1


def test_with_secondhand_lock_moves_lock_in_middle_to_end():
    middle = f"a. {SECONDHAND_LOCK} extra trailing words"
    out = with_secondhand_lock(middle)
    assert out.endswith(SECONDHAND_LOCK)
    assert out.count(SECONDHAND_LOCK) == 1
    assert out.startswith("a.")
    assert "extra trailing words" in out
    assert out.index("extra trailing words") < out.index(SECONDHAND_LOCK)


def test_with_secondhand_lock_collapses_multiple_locks_to_one():
    out = with_secondhand_lock(f"{SECONDHAND_LOCK} bg. {SECONDHAND_LOCK}")
    assert out == "bg. " + SECONDHAND_LOCK


def test_with_secondhand_lock_strips_surrounding_whitespace():
    assert with_secondhand_lock("bg.  \n\t") == "bg. " + SECONDHAND_LOCK
    assert with_secondhand_lock("  \n bg.") == "bg. " + SECONDHAND_LOCK


@pytest.mark.parametrize("empty", ["", "   ", "\n\t "])
def test_with_secondhand_lock_on_empty_or_whitespace_returns_lock_only(empty):
    assert with_secondhand_lock(empty) == SECONDHAND_LOCK


def test_with_secondhand_lock_on_lock_only_returns_lock_only():
    assert with_secondhand_lock(SECONDHAND_LOCK) == SECONDHAND_LOCK
    assert with_secondhand_lock(f"  {SECONDHAND_LOCK}  ") == SECONDHAND_LOCK


@pytest.mark.parametrize("key", list(PRESETS.keys()))
def test_get_preset_appends_lock_when_console_text_lacks_it(monkeypatch, key):
    calls = []

    def fake_get_prompt_text(name, fallback, **variables):
        calls.append(name)
        return "console-edited background without lock."

    monkeypatch.setattr(presets_mod, "get_prompt_text", fake_get_prompt_text)
    prompt = get_preset(key)["prompt"]
    assert calls == [f"preset_{key}"]
    assert prompt.startswith("console-edited background without lock.")
    assert prompt.endswith(SECONDHAND_LOCK)
    assert prompt.count(SECONDHAND_LOCK) == 1


@pytest.mark.parametrize("key", list(PRESETS.keys()))
def test_get_preset_does_not_double_lock_legacy_console_text(monkeypatch, key):
    legacy = f"legacy v1 background. {SECONDHAND_LOCK}"
    monkeypatch.setattr(presets_mod, "get_prompt_text",
                        lambda name, fallback, **v: legacy)
    assert get_preset(key)["prompt"] == legacy


@pytest.mark.parametrize("key", list(PRESETS.keys()))
def test_get_preset_moves_lock_to_end_when_console_text_appends_after_it(monkeypatch, key):
    # 콘솔에서 잠금 뒤에 지시문을 덧붙여도 잠금이 마지막 문장이 된다
    edited = f"bg. {SECONDHAND_LOCK} Also restore scratches."
    monkeypatch.setattr(presets_mod, "get_prompt_text",
                        lambda name, fallback, **v: edited)
    prompt = get_preset(key)["prompt"]
    assert prompt.endswith(SECONDHAND_LOCK)
    assert prompt.count(SECONDHAND_LOCK) == 1



# ══ 10-03: 팔 물건 고르기 — leave_out (count_lock 은 효과가 없어 뺐다) ═══════
from app.prompts.presets import MAX_LEAVE_OUT, TEXT_LOCK_MAX_CHARS, leave_out  # noqa: E402


@pytest.mark.parametrize("names", [[], ["", "  ", "\n"]])
def test_leave_out_nothing_usable_is_empty(names):
    assert leave_out(names) == ""


def test_leave_out_lists_names_in_order():
    assert leave_out(["keyboard", "mug"]) == \
        "\n\nLeave out these things that are not for sale: \"keyboard\", \"mug\"."


def test_leave_out_drops_blank_and_sanitizes():
    out = leave_out(["", ' mouse "pad"\n', "  "])
    assert out == "\n\nLeave out these things that are not for sale: \"mouse 'pad'\"."


def test_leave_out_caps_at_max_after_dropping_blanks():
    names = [""] * 5 + [f"o{i}" for i in range(MAX_LEAVE_OUT + 5)]
    out = leave_out(names)
    listed = [n.strip('"') for n in out.split(": ", 1)[1].rstrip(".").split(", ")]
    assert listed == [f"o{i}" for i in range(MAX_LEAVE_OUT)]


def test_leave_out_none_becomes_literal_none():
    """현재 동작 기록: prompt_safe(None) == "None" 이라 None 이 이름으로 들어간다
    (pipeline 이 넘기는 이름은 detector 가 비지 않은 문자열만 남기므로 실제로는 안 생김)."""
    assert leave_out([None]).endswith(': "None".')
