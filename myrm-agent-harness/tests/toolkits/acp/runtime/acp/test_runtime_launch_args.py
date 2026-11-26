"""What a started ACP agent really receives: the real runtime spawns the scripted agent and the agent reports its argv.

The command is a one-line wrapper named like a known backend, so the runtime recognises it and applies what it applies
to a real ``gemini``; an unknown name shows the control (nothing is dropped).
"""

from __future__ import annotations

import json
import logging
import stat
import sys
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import PermissionMode, RuntimeConfig, RuntimeEventType

from ._fakes import MOCK_AGENT

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the stand-in command is a shell script")


def _command(directory: Path, name: str) -> Path:
    wrapper = directory / name
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{MOCK_AGENT}" "$@"\n', encoding="utf-8")
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR)
    return wrapper


async def _started_with(command: Path, args: list[str], mode: PermissionMode, workspace: Path) -> list[str]:
    pool = RuntimePool(max_concurrent=1)
    pool.register(
        "mock",
        RuntimeConfig(backend_type="acp", command=str(command), args=args, timeout_seconds=60, permission_mode=mode),
    )
    text: list[str] = []
    try:
        async for event in pool.run_turn("mock", "/argv", session_id="chat-1", workspace=str(workspace)):
            if event.type is RuntimeEventType.ERROR:
                pytest.fail(f"the turn failed: {event.data}")
            if event.type is RuntimeEventType.TEXT_DELTA:
                text.append(str(event.data["content"]))
    finally:
        await pool.close_all()
    argv: list[str] = json.loads("".join(text).removeprefix("[argv] "))
    return argv


@pytest.mark.parametrize("mode", ["safe", "allow_all"])
async def test_a_known_agent_is_started_without_the_flags_that_skip_asking(
    tmp_path: Path, mode: PermissionMode, caplog: pytest.LogCaptureFixture
) -> None:
    command = _command(tmp_path, "gemini")

    with caplog.at_level(logging.WARNING):
        argv = await _started_with(command, ["--yolo", "--approval-mode", "yolo", "--images"], mode, tmp_path)

    assert argv == ["--images"]
    assert "acp_approval_flags_dropped" in caplog.text
    assert "--yolo" in caplog.text


async def test_an_unknown_agent_is_started_with_its_args_as_configured(tmp_path: Path) -> None:
    command = _command(tmp_path, "my-agent")

    argv = await _started_with(command, ["--yolo", "--images"], "safe", tmp_path)

    assert argv == ["--yolo", "--images"]
