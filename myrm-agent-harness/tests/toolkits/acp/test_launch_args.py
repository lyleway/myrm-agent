"""How ``resolve_cli_args`` merges a CLI's baseline launch args with the user's configured args.

A config that restates a baseline option adds nothing; options the config repeats on its own (``--add-dir a --add-dir
b``) are separate settings and all reach the CLI, each flag next to its value.
"""

from __future__ import annotations

import pytest

from myrm_agent_harness.toolkits.acp.drivers import cli_launch_args, resolve_cli_args

_MODES = ["safe", "allow_all"]

# A stored config that restates the baseline of claude in its own args, as one written by hand does.
_CLAUDE_PRESET = ["-p", "--output-format", "stream-json"]


@pytest.mark.parametrize("mode", _MODES)
def test_a_preset_that_restates_the_baseline_adds_nothing(mode: str) -> None:
    assert resolve_cli_args("claude", _CLAUDE_PRESET, mode) == resolve_cli_args("claude", [], mode)


@pytest.mark.parametrize("mode", _MODES)
def test_every_repeated_config_flag_keeps_its_value(mode: str) -> None:
    configured = [*_CLAUDE_PRESET, "--add-dir", "../a", "--add-dir", "../b"]

    resolved = resolve_cli_args("claude", configured, mode)

    assert resolved == [*resolve_cli_args("claude", [], mode), "--add-dir", "../a", "--add-dir", "../b"]


def test_a_restated_baseline_option_is_removed_wherever_it_stands() -> None:
    configured = ["--model", "opus", "--output-format", "stream-json", "-p"]

    assert resolve_cli_args("claude", configured, "safe") == [
        *resolve_cli_args("claude", [], "safe"),
        "--model",
        "opus",
    ]


def test_a_flag_is_only_restated_together_with_its_value() -> None:
    # The user asks for json output: that is a different option than the baseline's stream-json, so it stays whole.
    resolved = resolve_cli_args("claude", ["--output-format", "json"], "safe")

    assert resolved[-2:] == ["--output-format", "json"]
    assert resolved[: len(resolved) - 2] == resolve_cli_args("claude", [], "safe")


def test_the_configs_own_repeated_option_is_not_a_duplicate() -> None:
    assert resolve_cli_args("claude", ["--add-dir", "a", "--add-dir", "a"], "safe")[-4:] == [
        "--add-dir",
        "a",
        "--add-dir",
        "a",
    ]


def test_the_baseline_itself_is_never_repeated() -> None:
    resolved = resolve_cli_args("claude", cli_launch_args("claude"), "safe")

    assert resolved == resolve_cli_args("claude", [], "safe")


def test_an_unknown_cli_gets_its_args_untouched() -> None:
    assert resolve_cli_args("my-own-agent", ["-c", "a=1", "-c", "a=1"], "safe") == ["-c", "a=1", "-c", "a=1"]


@pytest.mark.parametrize("mode", ["ask", "bypass", "", "SAFE"])
def test_a_mode_nobody_defined_refuses_to_launch_instead_of_running_under_the_cli_default(mode: str) -> None:
    with pytest.raises(ValueError, match="Unknown permission mode"):
        resolve_cli_args("claude", [], mode)


def test_a_stale_permission_prompt_tool_in_the_config_cannot_replace_the_hosts() -> None:
    configured = [
        "-p",
        "--permission-prompt-tool",
        "mcp__other__approve",
        "--permission-prompt-tool=mcp__x__y",
        "--model",
        "m",
    ]

    resolved = resolve_cli_args("claude", configured, "safe")

    assert "--permission-prompt-tool" not in resolved
    assert not any(arg.startswith("--permission-prompt-tool=") for arg in resolved)
    assert "mcp__other__approve" not in resolved
    assert resolved[-2:] == ["--model", "m"]


def test_a_flag_the_mode_owns_replaces_the_stale_one_of_a_stored_config() -> None:
    # A stored bypass flag must be replaced by the selected mode's own permission choice.
    stale_bypass = resolve_cli_args("claude", ["--dangerously-skip-permissions"], "safe")
    assert "--dangerously-skip-permissions" not in stale_bypass
    assert stale_bypass[stale_bypass.index("--permission-mode") + 1] == "default"
    # The `--flag=value` spelling escapes a plain name lookup and must be stripped.
    claude_eq = resolve_cli_args("claude", ["--permission-mode=plan"], "allow_all")
    assert claude_eq.count("--permission-mode") == 1
    assert "--permission-mode=plan" not in claude_eq
    # A tool allowlist has no substitute in the mapping, so it must not be dropped.
    kept = resolve_cli_args("claude", ["--allowedTools", "Bash(git:*)"], "safe")
    assert kept[-2:] == ["--allowedTools", "Bash(git:*)"]


def test_a_mode_the_contract_does_not_name_refuses_to_launch() -> None:
    with pytest.raises(ValueError, match=r"Unknown permission mode 'plan'; expected one of \['allow_all', 'safe'\]"):
        resolve_cli_args("claude", [], "plan")
