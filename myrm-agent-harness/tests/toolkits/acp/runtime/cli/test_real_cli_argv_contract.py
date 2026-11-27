"""The whole command line of a turn, in every permission mode, is still one the real Claude CLI parses. Offline: no model.

A flag can be absent from ``--help`` and still be accepted (Claude hides ``--max-turns``), and a combination can be
refused although every flag is fine alone, so the help text is not the last word. The CLI itself is: each command line
the runtime builds is sent with the id of a session that does not exist, and a CLI that parsed it fails at the session
lookup (a non-zero exit before any model call), while one that did not names the argument it refused.

Run with ``-m integration``. A CLI that is not installed skips its tests, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names it.
"""

from __future__ import annotations

from pathlib import Path
from typing import get_args

import pytest

from myrm_agent_harness.toolkits.acp.runtime.cli.dialect import CliDialect, dialect_for
from myrm_agent_harness.toolkits.acp.runtime.cli.runtime import CliRuntime
from myrm_agent_harness.toolkits.acp.runtime.image_blocks import ImageBlock
from myrm_agent_harness.toolkits.acp.types import PermissionMode, RuntimeConfig
from tests.toolkits.acp.cli_contract import cli_binary, probe_env, run_probe

pytestmark = pytest.mark.integration

_UNKNOWN_ID = "11111111-2222-4333-8444-555555555555"
# What Claude says when it parsed the whole command line and found no conversation to resume.
_AT_SESSION_LOOKUP = "No conversation found"


def _launch(binary: str, mode: PermissionMode) -> tuple[CliDialect, list[str]]:
    """The command line ``CliRuntime`` builds for a turn in ``mode``, exactly as its own method assembles it."""
    config = RuntimeConfig(backend_type="cli", command=binary, permission_mode=mode, max_turns=3)
    runtime = CliRuntime("claude", config, workspace="/tmp/ws")
    dialect = dialect_for(binary)
    assert dialect is not None, "claude has no dialect"
    return dialect, runtime._launch_args(binary, dialect)


@pytest.fixture(scope="module")
def probe_home(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("cli-argv-home")


def _assert_reaches_the_session_lookup(argv: list[str], stdin_text: str | None, home: Path) -> None:
    code, output = run_probe(argv, probe_env(home), home, stdin_text=stdin_text)

    assert _AT_SESSION_LOOKUP in output, f"claude refused the command line (exit {code}):\n{output[:600]}"


@pytest.mark.parametrize("mode", get_args(PermissionMode))
def test_claude_parses_the_turn_command_line_of_every_permission_mode(mode: PermissionMode, probe_home: Path) -> None:
    binary = cli_binary("claude")
    dialect, args = _launch(binary, mode)
    resumed = dialect.with_resume(args, _UNKNOWN_ID)
    assert resumed is not None

    _assert_reaches_the_session_lookup(resumed, "hello", probe_home)


@pytest.mark.parametrize("mode", get_args(PermissionMode))
def test_claude_parses_the_image_turn_command_line_of_every_permission_mode(
    mode: PermissionMode, probe_home: Path
) -> None:
    binary = cli_binary("claude")
    dialect, args = _launch(binary, mode)
    block = ImageBlock(media_type="image/png", data="iVBORw0KGgo=")
    with_images, stdin_text = dialect.with_image_blocks(args, "what is this", [block])
    resumed = dialect.with_resume(with_images, _UNKNOWN_ID)
    assert resumed is not None

    _assert_reaches_the_session_lookup(resumed, stdin_text, probe_home)


def test_a_flag_the_cli_does_not_have_is_not_taken_for_a_turn(probe_home: Path) -> None:
    binary = cli_binary("claude")
    dialect, args = _launch(binary, "safe")
    resumed = dialect.with_resume([*args, "--not-a-flag-of-claude"], _UNKNOWN_ID)
    assert resumed is not None

    code, output = run_probe(resumed, probe_env(probe_home), probe_home, stdin_text="hello")

    assert code != 0 and _AT_SESSION_LOOKUP not in output and "--not-a-flag-of-claude" in output
