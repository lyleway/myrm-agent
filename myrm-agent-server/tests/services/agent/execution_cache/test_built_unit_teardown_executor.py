"""Evicting or deleting a pooled chat closes the executor its unit's agent was built with, and the shell in it.

A pooled unit keeps its agent, and with it the executor, across the messages of a chat. The end of a run closes only an
executor the run created, so the unit's teardown is the one place the chat's shell is closed.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from myrm_agent_harness.toolkits.code_execution.config import ExecutionConfig
from myrm_agent_harness.toolkits.code_execution.executors.local.executor import LocalExecutor
from myrm_agent_harness.toolkits.code_execution.executors.models import ExecutionContext

from app.services.agent.execution_cache.types import BuiltExecutionUnit


@pytest.mark.skipif(sys.platform == "win32", reason="drives a real bash shell")
async def test_teardown_ends_the_shell_of_the_executor_the_unit_was_built_with(tmp_path: Path) -> None:
    executor = LocalExecutor(ExecutionConfig(), workspace_path=str(tmp_path))
    started = await executor.execute_bash(
        ExecutionContext(
            code="echo started",
            work_dir=str(tmp_path),
            workspace_root=str(tmp_path),
            session_id="chat-1",
            timeout=30,
        )
    )
    assert started.success, started.error
    (session,) = executor._bash_sessions.values()
    shell = session.process  # a closed session lets go of its process, so keep it to see how it ended
    assert shell is not None and shell.returncode is None
    unit = BuiltExecutionUnit(skill_agent=MagicMock(executor=executor, close=AsyncMock()))

    await unit.teardown()
    await unit.teardown()  # an eviction that overlaps a delete tears the same unit down twice

    assert shell.returncode is not None, "the shell of the chat is still running"
    assert not executor._bash_sessions


async def test_teardown_closes_the_browser_and_desktop_then_the_executor_then_the_agent() -> None:
    order: list[str] = []

    def closing(name: str) -> AsyncMock:
        return AsyncMock(side_effect=lambda: order.append(name))

    skill_agent = MagicMock(executor=MagicMock(close=closing("executor")), close=closing("agent"))
    unit = BuiltExecutionUnit(
        skill_agent=skill_agent,
        browser_session=MagicMock(close=closing("browser")),
        desktop_session=MagicMock(close=MagicMock(side_effect=lambda: order.append("desktop"))),
    )

    await unit.teardown()

    assert order == ["browser", "desktop", "executor", "agent"]


async def test_teardown_of_an_agent_without_an_executor_still_closes_the_agent() -> None:
    skill_agent = MagicMock(executor=None, close=AsyncMock())

    await BuiltExecutionUnit(skill_agent=skill_agent).teardown()

    skill_agent.close.assert_awaited_once()


async def test_an_executor_that_fails_to_close_is_logged_and_does_not_keep_the_agent_open(
    caplog: pytest.LogCaptureFixture,
) -> None:
    executor = MagicMock(close=AsyncMock(side_effect=RuntimeError("shell would not close")))
    skill_agent = MagicMock(executor=executor, close=AsyncMock())

    with caplog.at_level(logging.WARNING, logger="app.services.agent.execution_cache.types"):
        await BuiltExecutionUnit(skill_agent=skill_agent).teardown()

    skill_agent.close.assert_awaited_once()
    (record,) = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert record.exc_info is not None and "shell would not close" in str(record.exc_info[1])
