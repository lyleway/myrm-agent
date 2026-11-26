"""``resolve_acp_args`` and ``effective_transport``: how a known agent is driven, from its contract.

A known ACP agent starts with its contract's entry args and without the configured flags that let it skip asking; every
other command, an adapter that merely carries an agent's name included, keeps its args exactly as configured.
"""

from __future__ import annotations

import pytest

from myrm_agent_harness.toolkits.acp.auth._launch_args import resolve_acp_args, resolve_cli_args
from myrm_agent_harness.toolkits.acp.auth._profiles import cli_launch_args, effective_transport

_CONTRACT = ["--acp", "--approval-mode", "default"]
_ACP_AGENTS = ["gemini", "qwen"]


@pytest.mark.parametrize("agent", _ACP_AGENTS)
def test_a_known_acp_agent_starts_with_its_contract_args(agent: str) -> None:
    launch = resolve_acp_args(agent, [])

    assert launch.args == _CONTRACT
    assert launch.dropped == []


@pytest.mark.parametrize("agent", _ACP_AGENTS)
@pytest.mark.parametrize(
    ("configured", "kept", "dropped"),
    [
        (["--yolo"], [], ["--yolo"]),
        (["-y"], [], ["-y"]),
        (["--approval-mode", "yolo"], [], ["--approval-mode", "yolo"]),
        (["--approval-mode=auto_edit"], [], ["--approval-mode=auto_edit"]),
        (["--model", "m", "--yolo", "-c", "a=1"], ["--model", "m", "-c", "a=1"], ["--yolo"]),
    ],
)
def test_the_flags_that_skip_asking_are_dropped_and_named(
    agent: str, configured: list[str], kept: list[str], dropped: list[str]
) -> None:
    launch = resolve_acp_args(agent, configured)

    assert launch.args == [*_CONTRACT, *kept]
    assert launch.dropped == dropped


@pytest.mark.parametrize("sandbox", [["-s"], ["--sandbox=true"], ["--sandbox"]])
def test_an_agents_own_sandbox_toggle_is_not_an_approval_flag(sandbox: list[str]) -> None:
    assert resolve_acp_args("gemini", sandbox).args == [*_CONTRACT, *sandbox]


@pytest.mark.parametrize("agent", _ACP_AGENTS)
def test_the_args_an_older_cli_preset_stored_stay(agent: str) -> None:
    """Both real agents start with them (``test_real_acp_session_mode``), so stored configs need no migration."""
    launch = resolve_acp_args(agent, ["--output-format", "stream-json"])

    assert launch.args == [*_CONTRACT, "--output-format", "stream-json"]
    assert launch.dropped == []


@pytest.mark.parametrize("command", ["gemini", "/opt/node/bin/gemini", "gemini.cmd", "GEMINI"])
def test_the_executable_name_decides_wherever_the_agent_lives(command: str) -> None:
    assert resolve_acp_args(command, ["--yolo"]).args == _CONTRACT


@pytest.mark.parametrize("agent", _ACP_AGENTS)
def test_a_configured_entry_flag_is_not_repeated(agent: str) -> None:
    assert resolve_acp_args(agent, ["--acp"]).args == _CONTRACT


@pytest.mark.parametrize(
    "command", ["my-agent", "opencode", "claude-code-acp", "gemini-wrapper", "codex-acp", "claude", "codex"]
)
def test_any_other_command_keeps_its_args(command: str) -> None:
    configured = ["--yolo", "--approval-mode", "yolo", "--acp"]

    launch = resolve_acp_args(command, configured)

    assert launch.args == configured
    assert launch.dropped == []


def test_the_configured_list_is_not_modified() -> None:
    configured = ["--yolo", "--acp"]

    resolve_acp_args("gemini", configured)

    assert configured == ["--yolo", "--acp"]


@pytest.mark.parametrize(
    ("command", "configured", "expected"),
    [
        ("gemini", "cli", "acp"),
        ("/usr/local/bin/qwen", "cli", "acp"),
        ("gemini.cmd", "cli", "acp"),
        ("claude", "acp", "cli"),
        ("/opt/bin/codex", "acp", "cli"),
        ("gemini-wrapper", "cli", "cli"),
        ("claude-code-acp", "acp", "acp"),
        ("my-agent", "acp", "acp"),
        ("my-agent", "cli", "cli"),
        (None, "acp", "acp"),
        # A value nobody recognises is the layer that stored it to report: only a known agent's contract replaces it.
        ("my-agent", "grpc", "grpc"),
        ("gemini", "grpc", "acp"),
    ],
)
def test_a_known_agent_runs_over_its_contract_transport(command: str | None, configured: str, expected: str) -> None:
    assert effective_transport(command, configured) == expected


@pytest.mark.parametrize("command", [*_ACP_AGENTS, "gemini-wrapper"])
def test_the_cli_channel_does_not_own_an_acp_agent(command: str) -> None:
    configured = ["--foo", "bar"]

    assert resolve_cli_args(command, configured, "safe") == configured
    assert cli_launch_args(command) == []


def test_the_cli_channel_still_owns_the_sandbox_of_a_cli_agent() -> None:
    resolved = resolve_cli_args("codex", ["--sandbox", "danger-full-access"], "safe")

    assert "danger-full-access" not in resolved
    assert resolved.count("--sandbox") == 1
