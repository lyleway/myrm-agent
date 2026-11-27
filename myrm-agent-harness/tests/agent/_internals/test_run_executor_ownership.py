"""A run's end closes the executor the run created itself, and never one it was handed.

``setup_workspace`` builds an executor only for a run that was given none, and the run's end closes that one. An
executor a run was handed belongs to whoever built it (a pooled chat's unit, a parent agent, a host application): the
end of a run leaves it open, whatever session id the run carries and however many runs share it. The agent runs
below are real ``BaseAgent.run`` calls, so a run's start and end meet in the same context as in production, and the
shell tests use the real local executor and real bash shells; only the model is scripted.
"""

from __future__ import annotations

import asyncio
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from myrm_agent_harness.agent._internals.run_lifecycle import cleanup_run, setup_workspace
from myrm_agent_harness.agent.base_agent import BaseAgent
from myrm_agent_harness.agent.middlewares.completion.completion_guard import reset_completion_guard
from myrm_agent_harness.agent.types import AgentRunStatistics, AgentRuntimeConfig, WorkspaceBinding
from myrm_agent_harness.core.security.types import SecurityConfig
from myrm_agent_harness.toolkits.code_execution.config import ExecutionConfig
from myrm_agent_harness.toolkits.code_execution.executors.base import CodeExecutor
from myrm_agent_harness.toolkits.code_execution.executors.local.executor import LocalExecutor
from myrm_agent_harness.toolkits.code_execution.executors.models import ExecutionContext, ExecutionResult
from tests.support.marked_processes import wait_until
from tests.toolkits.code_execution._session_helpers import hold_command

_SETTLE_S = 10
# How long a command that must not be cut is watched: a close that was going to cut it does so within milliseconds.
_WATCH_S = 1.0
_real_shell = pytest.mark.skipif(sys.platform == "win32", reason="drives a real bash shell")


class _RecordingExecutor(CodeExecutor):
    """An executor that counts its closes and says when one has run."""

    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0
        self.closed = asyncio.Event()

    async def execute(self, context: ExecutionContext) -> ExecutionResult:
        raise NotImplementedError

    async def execute_bash(self, context: ExecutionContext) -> ExecutionResult:
        raise NotImplementedError

    async def close(self) -> None:
        self.close_calls += 1
        self.closed.set()


@contextmanager
def _builds(executor: CodeExecutor) -> Iterator[None]:
    """``setup_workspace`` builds ``executor`` for a run that was handed none."""
    with patch("myrm_agent_harness.toolkits.code_execution.create_executor", return_value=executor):
        yield


def _workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


def _run_context(tmp_path: Path, session_id: str | None = "chat-a") -> dict[str, object]:
    context: dict[str, object] = {
        "workspaces_storage_root": str(tmp_path / "storage"),
        "workspace_binding": WorkspaceBinding(mode="chat", root_path=str(_workspace(tmp_path)), chat_id="chat-a"),
    }
    if session_id is not None:
        context["session_id"] = session_id
    return context


async def _start(executor: CodeExecutor | None, tmp_path: Path, session_id: str | None = "chat-a") -> dict[str, object]:
    merged, _ = await setup_workspace(executor, _run_context(tmp_path, session_id))
    return merged


def _end(merged: dict[str, object]) -> None:
    cleanup_run(AgentRunStatistics(), time.time(), None, None, lambda include_detached=True: 0, merged_context=merged)


async def _whole_run(executor: CodeExecutor, tmp_path: Path) -> None:
    _end(await _start(executor, tmp_path))


async def _let_scheduled_work_run() -> None:
    """A close the run's end scheduled runs within a few loop passes, so one that should not be there shows."""
    for _ in range(3):
        await asyncio.sleep(0)


async def _closed_within_the_limit(executor: _RecordingExecutor) -> None:
    async with asyncio.timeout(_SETTLE_S):
        await executor.closed.wait()


async def test_the_end_of_a_run_leaves_an_executor_it_was_handed_open(tmp_path: Path) -> None:
    executor = _RecordingExecutor()

    _end(await _start(executor, tmp_path))
    await _let_scheduled_work_run()

    assert executor.close_calls == 0


async def test_the_end_of_a_run_closes_the_executor_it_created_once(tmp_path: Path) -> None:
    executor = _RecordingExecutor()
    with _builds(executor):
        merged = await _start(None, tmp_path)

    _end(merged)
    _end(merged)
    await _closed_within_the_limit(executor)
    await _let_scheduled_work_run()

    assert executor.close_calls == 1


async def test_a_cleanup_step_that_fails_does_not_keep_the_executor_it_created_open(tmp_path: Path) -> None:
    executor = _RecordingExecutor()
    with _builds(executor):
        merged = await _start(None, tmp_path)

    with patch(
        "myrm_agent_harness.agent._internals.run_lifecycle.collect_tracker_stats",
        side_effect=RuntimeError("stats broke"),
    ):
        _end(merged)

    await _closed_within_the_limit(executor)


async def test_a_run_without_a_session_id_still_closes_the_executor_it_created(tmp_path: Path) -> None:
    executor = _RecordingExecutor()
    with _builds(executor):
        merged = await _start(None, tmp_path, session_id=None)

    _end(merged)

    await _closed_within_the_limit(executor)


async def test_a_child_run_handed_its_parents_executor_does_not_close_it(tmp_path: Path) -> None:
    """A child's task starts from a copy of its parent's context, which holds the parent's claim on its executor."""
    parent = _RecordingExecutor()
    with _builds(parent):
        parent_run = await _start(None, tmp_path, "chat-parent")

    await asyncio.create_task(_whole_run(parent, tmp_path))
    await _let_scheduled_work_run()
    assert parent.close_calls == 0, "the end of the child closed the executor its parent created"

    _end(parent_run)
    await _closed_within_the_limit(parent)
    assert parent.close_calls == 1


async def test_a_run_that_fails_to_start_closes_the_executor_it_created(tmp_path: Path) -> None:
    executor = _RecordingExecutor()
    with (
        _builds(executor),
        patch(
            "myrm_agent_harness.agent.security.workspace_trust.runtime.apply_workspace_trust_for_root",
            side_effect=RuntimeError("trust refused"),
        ),
        pytest.raises(RuntimeError, match="trust refused"),
    ):
        await _start(None, tmp_path)

    await _closed_within_the_limit(executor)
    _end({})
    await _let_scheduled_work_run()
    assert executor.close_calls == 1


async def test_a_run_that_fails_to_start_leaves_an_executor_it_was_handed_open(tmp_path: Path) -> None:
    executor = _RecordingExecutor()
    with (
        patch(
            "myrm_agent_harness.agent.security.workspace_trust.runtime.apply_workspace_trust_for_root",
            side_effect=RuntimeError("trust refused"),
        ),
        pytest.raises(RuntimeError, match="trust refused"),
    ):
        await _start(executor, tmp_path)

    await _let_scheduled_work_run()

    assert executor.close_calls == 0


class _ScriptedModel(FakeMessagesListChatModel):
    def bind_tools(self, tools: object, **kwargs: object) -> _ScriptedModel:
        return self


def _agent(executor: CodeExecutor | None, tmp_path: Path) -> BaseAgent:
    model = _ScriptedModel(responses=[AIMessage(content="done")] * 6)
    config = AgentRuntimeConfig(
        recursion_limit=50, security_config=SecurityConfig.workspace(allowed_roots=(str(_workspace(tmp_path)),))
    )
    return BaseAgent(llm=model, executor=executor, tools=[], config=config)


async def _drain(agent: BaseAgent, tmp_path: Path) -> None:
    reset_completion_guard()
    async for _ in agent.run("say done", context=_run_context(tmp_path)):
        pass


async def test_an_agent_run_closes_the_executor_it_created_when_its_stream_ends(tmp_path: Path) -> None:
    executor = _RecordingExecutor()
    with _builds(executor):
        await _drain(_agent(None, tmp_path), tmp_path)

    await _closed_within_the_limit(executor)
    assert executor.close_calls == 1


async def test_the_runs_of_an_agent_leave_the_executor_it_was_given_open(tmp_path: Path) -> None:
    executor = _RecordingExecutor()
    agent = _agent(executor, tmp_path)

    for _ in range(3):
        await _drain(agent, tmp_path)
        await _let_scheduled_work_run()

    assert executor.close_calls == 0


@pytest.fixture
async def local_executor(tmp_path: Path) -> AsyncIterator[LocalExecutor]:
    executor = LocalExecutor(ExecutionConfig(), workspace_path=str(_workspace(tmp_path)))
    try:
        yield executor
    finally:
        await executor.close()


def _command(code: str, tmp_path: Path) -> ExecutionContext:
    workspace = str(_workspace(tmp_path))
    return ExecutionContext(code=code, work_dir=workspace, workspace_root=workspace, session_id="chat-a", timeout=60)


@_real_shell
async def test_every_run_of_a_chat_uses_the_shell_the_first_run_started(
    local_executor: LocalExecutor, tmp_path: Path
) -> None:
    """A pooled chat hands the run of each message the same executor, so its shell has to outlive the runs."""
    shells: list[str] = []
    for _ in range(3):
        merged = await _start(local_executor, tmp_path)
        # A command is a child of its shell, so its parent pid names the shell (``$$`` is the command's own pid).
        result = await local_executor.execute_bash(_command("echo $PPID", tmp_path))
        assert result.success, result.error
        shells.append(result.stdout.strip())
        _end(merged)
        await _let_scheduled_work_run()

    assert len(set(shells)) == 1, f"the shell changed between runs: {shells}"


@_real_shell
async def test_a_run_ending_on_a_shared_executor_does_not_cut_a_command_another_run_has_in_flight(
    local_executor: LocalExecutor, tmp_path: Path
) -> None:
    started, gate = _workspace(tmp_path) / "started", _workspace(tmp_path) / "gate"
    first = await _start(local_executor, tmp_path)
    running = asyncio.create_task(local_executor.execute_bash(_command(hold_command(started, gate), tmp_path)))
    await wait_until(started.exists)

    await asyncio.create_task(_whole_run(local_executor, tmp_path))

    done, _ = await asyncio.wait({running}, timeout=_WATCH_S)
    if done:
        pytest.fail(f"the end of the other run cut the command: exit code {running.result().exit_code}")
    gate.touch()
    async with asyncio.timeout(_SETTLE_S):
        result = await running
    assert result.success and result.stdout.strip() == "released"
    _end(first)
