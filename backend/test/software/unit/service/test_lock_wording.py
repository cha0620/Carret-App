"""SECONDHAND_LOCK · text_lock 문구 (10-01 리뷰) — 정리는 하되 없는 걸 더하지 않고 물건은 그대로 (각도 · 가림은 업로드 단계가 맡는다)."""
from app.prompts.presets import SECONDHAND_LOCK, text_lock


def test_secondhand_lock_does_not_ask_to_straighten():
    """"straighten" 은 물건을 돌려 다른 면을 지어내게 만든다 — 빠졌어야 한다."""
    assert "straighten" not in SECONDHAND_LOCK.lower()


def test_secondhand_lock_core_rules():
    """줄인 잠금이 지키는 세 가지 — 없는 걸 더하지 않기, 하자 그대로, 고치지 않기."""
    assert "Do not add anything that is not in the original photo" in SECONDHAND_LOCK
    assert "in the same place and at the same size" in SECONDHAND_LOCK
    assert "Do not repair, clean or restore it" in SECONDHAND_LOCK


def test_previous_lock_is_stripped_from_old_prompts():
    """Langfuse 에 남은 직전 잠금(각도 유지 문구)이 새 잠금과 같이 붙지 않게."""
    from app.prompts.presets import LEGACY_LOCKS, with_secondhand_lock
    prev = next(l for l in LEGACY_LOCKS if l.startswith("Keep the camera angle"))
    assert with_secondhand_lock(f"bg. {prev}") == "bg. " + SECONDHAND_LOCK


def test_text_lock_wording():
    out = text_lock([{"text": "NIKE"}])
    assert ("same font, size and position on the product. Do not retype, restyle, translate, fix, "
            "move, duplicate, or add any text:") in out
    assert out.endswith('"NIKE".')


def test_strong_frame_lock_is_now_legacy_and_stripped():
    """10-03 낮 판(틀 지시가 센 판, "no text" 없음)이 Langfuse 에 남아 있으면 떼고 새 잠금 하나만."""
    from app.prompts.presets import LEGACY_LOCKS, with_secondhand_lock
    prev = next(l for l in LEGACY_LOCKS
                if l.startswith("Tidy it into a clean listing photo: center it, fill most of the frame")
                and "no other items" not in l)
    out = with_secondhand_lock(f"Pure white seamless background. {prev}")
    assert out == "Pure white seamless background. " + SECONDHAND_LOCK
    assert out.count("Tidy it into a clean listing photo") == 1 and "fill most of the frame" not in out


