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


def test_with_secondhand_lock_is_idempotent():
    once = with_secondhand_lock("white background.")
    assert with_secondhand_lock(once) == once
    assert with_secondhand_lock(with_secondhand_lock(once)) == once


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


# ══ 10-03: 팔 물건 고르기 — leave_out (count_lock 은 효과가 없어 뺐다) ═══════
from app.prompts.presets import LEAVE_OUT, leave_out  # noqa: E402


def test_leave_out_never_names_the_objects():
    """10-03: 이름을 쓰면 그 이름의 물건을 지운다 ("CD case" → 고른 CD 의 케이스까지) — 이름 없는 한 문장."""
    out = leave_out(["keyboard", "CD case", ' mouse "pad"\n'])
    assert out == LEAVE_OUT and "keyboard" not in out and "CD" not in out and "case" not in out.lower()
