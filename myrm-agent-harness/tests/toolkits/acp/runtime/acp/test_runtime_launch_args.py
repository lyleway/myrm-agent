"""What a started ACP agent really receives and how its session is held to the host's policy.

The real runtime spawns the scripted agent through a one-line wrapper named like a known backend, so the runtime
recognises it and applies what it applies to a real ``gemini``; an unknown name is the control. The agent reports its
own argv and session mode, so every assertion is about what the agent saw.
"""

from __future__ import annotations

import json
import logging
import stat
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    AcpError,
    AcpErrorCode,
    BackendType,
    PermissionMode,
    RuntimeConfig,
    RuntimeEventType,
)

from ._fakes import MOCK_AGENT

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stand-in command is a shell script")

_CONTRACT = ["--acp", "--approval-mode", "default"]


@dataclass(frozen=True)
class _Turn:
    text: str
    error: AcpError | None


def _command(directory: Path, name: str) -> Path:
    wrapper = directory / name
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{MOCK_AGENT}" "$@"\n', encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return wrapper


async def _turn(
    command: Path,
    args: list[str],
    workspace: Path,
    prompt: str,
    *,
    mode: PermissionMode = "safe",
    backend_type: BackendType = "acp",
) -> _Turn:
    pool = RuntimePool(max_concurrent=1)
    pool.register(
        "mock",
        RuntimeConfig(
            backend_type=backend_type, command=str(command), args=args, timeout_seconds=60, permission_mode=mode
        ),
    )
    text: list[str] = []
    error: AcpError | None = None
    try:
        async for event in pool.run_turn("mock", prompt, session_id="chat-1", workspace=str(workspace)):
            if event.type is RuntimeEventType.ERROR:
                error = event.data["error"]  # type: ignore[assignment]
            if event.type is RuntimeEventType.TEXT_DELTA:
                text.append(str(event.data["content"]))
    finally:
        await pool.close_all()
    return _Turn("".join(text), error)


async def _argv(command: Path, args: list[str], workspace: Path, **kwargs: object) -> list[str]:
    turn = await _turn(command, args, workspace, "/argv", **kwargs)  # type: ignore[arg-type]
    assert turn.error is None, turn.error
    argv: list[str] = json.loads(turn.text.removeprefix("[argv] "))
    return argv


@pytest.mark.parametrize("mode", ["safe", "allow_all"])
async def test_a_known_agent_is_started_with_its_contract_and_without_the_flags_that_skip_asking(
    tmp_path: Path, mode: PermissionMode, caplog: pytest.LogCaptureFixture
) -> None:
    command = _command(tmp_path, "gemini")

    with caplog.at_level(logging.WARNING):
        argv = await _argv(command, ["--yolo", "--approval-mode", "yolo", "--images"], tmp_path, mode=mode)

    assert argv == [*_CONTRACT, "--images"]
    assert "acp_approval_flags_dropped" in caplog.text
    assert "--yolo" in caplog.text


async def test_an_unknown_agent_is_started_with_its_args_as_configured(tmp_path: Path) -> None:
    command = _command(tmp_path, "my-agent")

    assert await _argv(command, ["--yolo", "--images"], tmp_path) == ["--yolo", "--images"]


async def test_an_adapter_that_only_carries_an_agents_name_is_not_that_agent(tmp_path: Path) -> None:
    command = _command(tmp_path, "gemini-wrapper")

    assert await _argv(command, ["--yolo", "--images"], tmp_path) == ["--yolo", "--images"]


async def test_a_known_acp_agent_registered_as_a_cli_runs_over_acp_all_the_same(tmp_path: Path) -> None:
    command = _command(tmp_path, "qwen")

    argv = await _argv(command, ["--images"], tmp_path, backend_type="cli")

    assert argv == [*_CONTRACT, "--images"]


def test_an_unknown_command_keeps_the_transport_it_was_registered_with(tmp_path: Path) -> None:
    pool = RuntimePool()
    pool.register("a", RuntimeConfig(backend_type="cli", command=str(tmp_path / "my-agent")))
    pool.register("b", RuntimeConfig(backend_type="cli", command=str(tmp_path / "gemini")))
    pool.register("c", RuntimeConfig(backend_type="acp", command=str(tmp_path / "claude")))

    assert [pool.get_config(name).backend_type for name in "abc"] == ["cli", "acp", "cli"]  # type: ignore[union-attr]


async def test_a_session_in_a_mode_that_never_asks_is_put_back_in_the_mode_that_asks(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    command = _command(tmp_path, "gemini")

    with caplog.at_level(logging.WARNING):
        turn = await _turn(command, ["--mode", "yolo"], tmp_path, "/mode")

    assert (turn.text, turn.error) == ("[mode] default", None)
    assert "acp_session_mode_reset" in caplog.text


async def test_a_session_already_in_the_mode_that_asks_is_left_alone(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    command = _command(tmp_path, "gemini")

    with caplog.at_level(logging.WARNING):
        turn = await _turn(command, ["--mode", "default"], tmp_path, "/mode")

    assert (turn.text, turn.error) == ("[mode] default", None)
    assert "acp_session_mode_reset" not in caplog.text


async def test_an_agent_that_refuses_to_leave_that_mode_is_not_run(tmp_path: Path) -> None:
    command = _command(tmp_path, "gemini")

    turn = await _turn(command, ["--mode", "yolo", "--keep-mode"], tmp_path, "/mode")

    assert turn.text == ""
    assert turn.error is not None
    assert "reports session mode 'yolo'" in turn.error.message
    assert "could not be put in 'default'" in turn.error.message


async def test_an_agent_that_offers_no_mode_that_asks_is_not_run(tmp_path: Path) -> None:
    command = _command(tmp_path, "gemini")

    turn = await _turn(command, ["--mode", "yolo", "--modes", "yolo,plan"], tmp_path, "/mode")

    assert turn.text == ""
    assert turn.error is not None
    assert "offers no mode 'default'" in turn.error.message


async def test_allow_all_does_not_care_which_mode_the_agent_is_in(tmp_path: Path) -> None:
    command = _command(tmp_path, "gemini")

    turn = await _turn(command, ["--mode", "yolo"], tmp_path, "/mode", mode="allow_all")

    assert (turn.text, turn.error) == ("[mode] yolo", None)


async def test_an_unknown_agent_has_no_mode_the_host_could_check(tmp_path: Path) -> None:
    command = _command(tmp_path, "my-agent")

    turn = await _turn(command, ["--mode", "yolo"], tmp_path, "/mode")

    assert (turn.text, turn.error) == ("[mode] yolo", None)


async def test_a_refused_login_ends_the_turn_as_an_authentication_failure(tmp_path: Path) -> None:
    command = _command(tmp_path, "gemini")

    turn = await _turn(command, ["--login-required"], tmp_path, "hello")

    assert turn.error is not None
    assert turn.error.code is AcpErrorCode.AUTH_FAILED
    assert turn.error.message == "Authentication required"


async def test_any_other_refusal_of_a_session_stays_unclassified(tmp_path: Path) -> None:
    command = _command(tmp_path, "gemini")

    turn = await _turn(command, ["--bad-session"], tmp_path, "hello")

    assert turn.error is not None
    assert turn.error.code is AcpErrorCode.UNKNOWN
