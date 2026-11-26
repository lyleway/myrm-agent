"""``without_approval_flags``: what an ACP agent is started with once the host's policy answers its requests.

The flags that let a known agent skip asking leave the launch args; sandbox flags, every other flag and an unknown
command's args stay exactly as configured.
"""

from __future__ import annotations

import pytest

from myrm_agent_harness.toolkits.acp.auth._profiles import resolve_cli_args, without_approval_flags


@pytest.mark.parametrize(
    ("command", "configured", "expected"),
    [
        ("gemini", ["--experimental-acp", "--yolo"], ["--experimental-acp"]),
        ("gemini", ["-y", "--experimental-acp"], ["--experimental-acp"]),
        ("gemini", ["--approval-mode", "yolo", "--experimental-acp"], ["--experimental-acp"]),
        ("gemini", ["--approval-mode=auto_edit", "--experimental-acp"], ["--experimental-acp"]),
        ("qwen", ["--acp", "--approval-mode", "yolo"], ["--acp"]),
        ("claude", ["--permission-mode", "bypassPermissions", "--dangerously-skip-permissions"], []),
        ("/opt/bin/claude-code-acp", ["--permission-mode=acceptEdits", "--verbose"], ["--verbose"]),
        ("codex", ["--yolo", "--full-auto", "--approve-for-me", "--dangerously-bypass-approvals-and-sandbox"], []),
    ],
)
def test_the_flags_that_skip_asking_are_dropped(command: str, configured: list[str], expected: list[str]) -> None:
    assert without_approval_flags(command, configured) == expected


@pytest.mark.parametrize(
    "sandbox",
    [["--sandbox", "read-only"], ["-s", "workspace-write"], ["--sandbox=read-only"]],
)
def test_a_sandbox_the_user_asked_for_stays(sandbox: list[str]) -> None:
    assert without_approval_flags("codex", ["--yolo", *sandbox]) == sandbox


def test_gemini_keeps_its_own_sandbox_toggle() -> None:
    assert without_approval_flags("gemini", ["-s", "--yolo"]) == ["-s"]


def test_other_flags_keep_their_values_and_their_order() -> None:
    configured = ["--model", "gpt-5", "--yolo", "-c", 'model_reasoning_effort="high"', "--acp"]

    assert without_approval_flags("gemini", configured) == [
        "--model",
        "gpt-5",
        "-c",
        'model_reasoning_effort="high"',
        "--acp",
    ]


@pytest.mark.parametrize("command", ["opencode", "my-agent", "/usr/local/bin/some-acp-agent"])
def test_an_unknown_command_is_left_alone(command: str) -> None:
    configured = ["--yolo", "--approval-mode", "yolo"]

    assert without_approval_flags(command, configured) == configured


def test_the_configured_list_is_not_modified() -> None:
    configured = ["--yolo", "--acp"]

    without_approval_flags("gemini", configured)

    assert configured == ["--yolo", "--acp"]


def test_the_cli_channel_still_owns_the_sandbox_flags() -> None:
    """The CLI mapping sets the sandbox itself, so it still strips what the user configured there."""
    resolved = resolve_cli_args("codex", ["--sandbox", "danger-full-access"], "safe")

    assert "danger-full-access" not in resolved
    assert resolved.count("--sandbox") == 1
