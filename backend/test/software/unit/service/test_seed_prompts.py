"""scripts/seed_langfuse_prompts.py drift-guard.

backend/app 전체에서 get_prompt_text(...) 호출에 쓰이는 Langfuse 프롬프트 이름을
소스(AST)에서 직접 찾아, 전부 prompts_to_seed() 에 있는지 확인한다. 새 프롬프트를
추가하고 시드 목록에 빠뜨리면 이 테스트가 실패한다."""
import ast
import importlib.util
from pathlib import Path

import pytest

import app.core.tracing as tracing
from app.prompts import (check_photo_template, detect_template, frag,
                         verify_template)
from app.prompts.presets import PRESETS, SECONDHAND_LOCK

BACKEND = Path(__file__).resolve().parents[5] / "backend"
APP_DIR = BACKEND / "app"
SEED_SCRIPT = BACKEND / "scripts" / "seed_langfuse_prompts.py"

EXPECTED_STATIC = {
    "classify", "detect_box", "detect", "verify", "item_text",
    "check_photo", "match", "judge_system", "auto_feedback_system",
}


@pytest.fixture(autouse=True)
def disable_langfuse(monkeypatch):
    monkeypatch.setattr(tracing, "_client", None, raising=False)
    monkeypatch.setattr(tracing, "_disabled", True, raising=False)
    yield


@pytest.fixture(scope="module")
def seed_module():
    spec = importlib.util.spec_from_file_location("seed_langfuse_prompts", SEED_SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def seeds(seed_module):
    return seed_module.prompts_to_seed()


def _call_name(node: ast.Call):
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return None


def discover_prompt_names():
    """(정적 이름 집합, preset_ f-string 사용 여부, 해석 불가 호출 목록)"""
    static, dynamic_preset, unresolved = set(), False, []
    for path in sorted(APP_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _call_name(node) == "get_prompt_text"):
                continue
            if not node.args:
                name_kw = [k for k in node.keywords if k.arg == "name"]
                arg = name_kw[0].value if name_kw else None
            else:
                arg = node.args[0]
            loc = f"{path.relative_to(BACKEND)}:{node.lineno}"
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                static.add(arg.value)
            elif (isinstance(arg, ast.JoinedStr) and len(arg.values) == 2
                  and isinstance(arg.values[0], ast.Constant)
                  and arg.values[0].value == "preset_"
                  and isinstance(arg.values[1], ast.FormattedValue)):
                dynamic_preset = True
            else:
                unresolved.append(loc)
    return static, dynamic_preset, unresolved


def test_discovery_finds_all_known_call_sites():
    static, dynamic_preset, unresolved = discover_prompt_names()
    # 탐색 자체가 망가지면(0개 발견 등) drift 검사가 헛돌기 때문에 기준선 확인
    assert EXPECTED_STATIC <= static
    assert dynamic_preset, "presets.py 의 get_prompt_text(f\"preset_{key}\") 를 찾지 못함"
    assert unresolved == [], (
        f"이름을 정적으로 알 수 없는 get_prompt_text 호출: {unresolved} — "
        "리터럴 이름을 쓰거나 이 테스트의 탐색 규칙을 확장할 것")


def test_every_runtime_prompt_name_is_seeded(seeds):
    static, dynamic_preset, _ = discover_prompt_names()
    expected = set(static)
    if dynamic_preset:
        expected |= {f"preset_{k}" for k in PRESETS}
    missing = sorted(expected - set(seeds))
    assert missing == [], f"prompts_to_seed() 에 빠진 프롬프트: {missing}"


def test_seed_has_no_names_unused_at_runtime(seeds):
    static, _, _ = discover_prompt_names()
    expected = static | {f"preset_{k}" for k in PRESETS}
    assert sorted(set(seeds) - expected) == []


def test_seed_templates_match_shared_template_functions(seeds):
    assert seeds["detect"] == detect_template()
    assert seeds["verify"] == verify_template()
    assert seeds["check_photo"] == check_photo_template()
    assert seeds["item_text"] == frag("item_text")


@pytest.mark.parametrize("key", list(PRESETS.keys()))
def test_seed_presets_are_lock_free_background_only(seeds, key):
    assert seeds[f"preset_{key}"] == PRESETS[key]["fallback_prompt"]
    assert SECONDHAND_LOCK not in seeds[f"preset_{key}"]


def test_seed_values_are_non_empty_strings(seeds):
    for name, text in seeds.items():
        assert isinstance(text, str) and text.strip(), name


class _NetworkTouched(Exception):
    pass


@pytest.fixture
def no_network_seed(seed_module, monkeypatch):
    """키는 있는 것처럼 두되 Langfuse 생성 시 즉시 실패 — 검증이 네트워크 전에 끝나는지 확인."""
    import sys
    import types

    def boom(*a, **k):
        raise _NetworkTouched("Langfuse constructed")

    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=boom))
    monkeypatch.setattr(seed_module.settings, "langfuse_public_key", "pk", raising=False)
    monkeypatch.setattr(seed_module.settings, "langfuse_secret_key", "sk", raising=False)
    return seed_module


def _run_main(mod, monkeypatch, argv):
    monkeypatch.setattr(mod.sys, "argv", ["seed", *argv])
    return mod.main()


@pytest.mark.parametrize("argv", [
    ["no_such_prompt"],
    ["detect", "no_such_prompt"],
    ["--all", "detect"],
    ["detect", "--all"],
    ["--all", "no_such_prompt"],
])
def test_main_rejects_bad_args_before_network(no_network_seed, monkeypatch, capsys, argv):
    with pytest.raises(SystemExit) as exc:
        _run_main(no_network_seed, monkeypatch, argv)
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert "detect" in out  # 가능한 이름 목록을 안내


def test_main_unknown_name_is_reported(no_network_seed, monkeypatch, capsys):
    with pytest.raises(SystemExit):
        _run_main(no_network_seed, monkeypatch, ["no_such_prompt"])
    assert "no_such_prompt" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [[], ["--all"], ["detect", "detect", "verify"]])
def test_main_valid_args_pass_validation(no_network_seed, monkeypatch, argv):
    # 유효 인자(중복 포함)는 검증을 통과해 Langfuse 생성 단계까지 간다
    with pytest.raises(_NetworkTouched):
        _run_main(no_network_seed, monkeypatch, argv)


def test_main_valid_args_without_keys_exit_before_network(seed_module, monkeypatch, capsys):
    import sys
    import types

    def boom(*a, **k):
        raise _NetworkTouched("Langfuse constructed")

    monkeypatch.setitem(sys.modules, "langfuse", types.SimpleNamespace(Langfuse=boom))
    monkeypatch.setattr(seed_module.settings, "langfuse_public_key", "", raising=False)
    monkeypatch.setattr(seed_module.settings, "langfuse_secret_key", "", raising=False)
    with pytest.raises(SystemExit) as exc:
        _run_main(seed_module, monkeypatch, ["detect"])
    assert exc.value.code == 1
    assert "LANGFUSE_PUBLIC_KEY" in capsys.readouterr().out


def test_main_help_exits_cleanly_without_network(no_network_seed, monkeypatch, capsys):
    assert _run_main(no_network_seed, monkeypatch, ["--help"]) is None
    assert capsys.readouterr().out.strip()
