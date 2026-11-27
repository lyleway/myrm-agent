"""One shell per run identity, however many commands of the run arrive together.

The parallel sub-agents of a run share its shell. These tests drive the registry the local executor keeps, with real
shells: commands that arrive together start one shell, a caller that checks a shell another command is running
gets it at once, neither waiting for that command nor restarting its shell, and a shell the registry drops is closed,
so a command that waited on it runs once, on the shell that replaced it. A request stopped while its first shell
starts leaves no shell behind: the registry keeps a session only once it has started, so nothing else would close it.
"""

from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path

import pytest

from myrm_agent_harness.core.security.types import AccessRoot
from myrm_agent_harness.toolkits.code_execution.config import ExecutionConfig
from myrm_agent_harness.toolkits.code_execution.executors.local import _bash_sessions
from myrm_agent_harness.toolkits.code_execution.executors.local.executor import LocalExecutor
from myrm_agent_harness.toolkits.code_execution.executors.models import ExecutionContext
from myrm_agent_harness.toolkits.code_execution.session import LocalPersistentSession
from myrm_agent_harness.toolkits.code_execution.session.command_stream import SESSION_CLOSED_BEFORE_START
from myrm_agent_harness.toolkits.code_execution.session.models import SessionExecutionResult
from tests.support.marked_processes import wait_until
from tests.toolkits.code_execution._session_helpers import hold_command, make_config

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="drives a real bash shell")

_SETTLE_S = 10
# A variable the shell is started with: which shell a command ran in is the value it prints.
_SHELL_MARK = "MYRM_TEST_SHELL"


@pytest.fixture
async def executor(tmp_path: Path) -> AsyncIterator[LocalExecutor]:
    local = LocalExecutor(ExecutionConfig(), workspace_path=str(tmp_path))
    try:
        yield local
    finally:
        await local.close()


def _context(session_id: str) -> ExecutionContext:
    return ExecutionContext(code="true", session_id=session_id)


@dataclass(frozen=True)
class _BusyShell:
    context: ExecutionContext
    session: LocalPersistentSession
    gate: Path
    running: asyncio.Task[SessionExecutionResult]


async def _busy_shell(executor: LocalExecutor, tmp_path: Path) -> _BusyShell:
    """A shell that is running a command which holds it until the test creates ``gate``."""
    context = _context("busy")
    session = await executor._get_or_create_bash_session(context, executor._build_bash_env(None))
    started, gate = tmp_path / "started", tmp_path / "gate"
    running = asyncio.create_task(session.execute(hold_command(started, gate)))
    await wait_until(started.exists)
    return _BusyShell(context, session, gate, running)


async def test_commands_that_arrive_together_start_one_shell(
    executor: LocalExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[int] = []
    create_process = LocalPersistentSession._create_process

    async def counting(self: LocalPersistentSession) -> asyncio.subprocess.Process:
        process = await create_process(self)
        spawned.append(process.pid)
        return process

    monkeypatch.setattr(LocalPersistentSession, "_create_process", counting)

    env = executor._build_bash_env(None)
    sessions = await asyncio.gather(*(executor._get_or_create_bash_session(_context("shared"), env) for _ in range(4)))

    assert len({id(session) for session in sessions}) == 1
    assert len(spawned) == 1
    assert len(executor._bash_sessions) == 1


async def test_a_caller_that_checks_a_busy_shell_neither_waits_for_its_command_nor_restarts_it(
    executor: LocalExecutor, tmp_path: Path
) -> None:
    busy = await _busy_shell(executor, tmp_path)
    assert busy.session.process is not None
    pid = busy.session.process.pid

    async with asyncio.timeout(_SETTLE_S):
        checked = await executor._get_or_create_bash_session(busy.context, executor._build_bash_env(None))

    assert checked is busy.session
    assert not busy.running.done(), "the check came back while the command was still running"
    assert busy.session.process is not None and busy.session.process.pid == pid, "the shell was not restarted"

    busy.gate.touch()
    async with asyncio.timeout(_SETTLE_S):
        result = await busy.running
    assert result.success and result.stdout == "released", "the command that was running was not interrupted"


async def test_a_shell_that_is_no_longer_alive_is_closed_when_its_replacement_is_started(
    executor: LocalExecutor,
) -> None:
    context, env = _context("dead"), executor._build_bash_env(None)
    dead = await executor._get_or_create_bash_session(context, env)
    assert dead.process is not None
    dead.process.kill()
    await dead.process.wait()

    try:
        replacement = await executor._get_or_create_bash_session(context, env)

        assert replacement is not dead
        refused = await dead.execute("echo orphan")
        assert refused.error == SESSION_CLOSED_BEFORE_START, "the dropped shell was not closed, so a command revived it"
    finally:
        await dead.close()


async def test_a_shell_that_fails_its_health_check_is_closed_when_its_replacement_is_started(
    executor: LocalExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    context, env = _context("sick"), executor._build_bash_env(None)
    sick = await executor._get_or_create_bash_session(context, env)

    async def failing_probe() -> bool:
        return False

    monkeypatch.setattr(sick, "check_health", failing_probe)
    try:
        replacement = await executor._get_or_create_bash_session(context, env)

        assert replacement is not sick
        assert len(executor._bash_sessions) == 1
        refused = await sick.execute("echo orphan")
        assert refused.error == SESSION_CLOSED_BEFORE_START, "the dropped shell was not closed, so a command revived it"
    finally:
        await sick.close()


async def test_a_command_queued_on_a_shell_that_was_replaced_runs_on_the_shell_that_replaced_it(
    executor: LocalExecutor, tmp_path: Path, outside_tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = outside_tmp_path / "first", outside_tmp_path / "second"
    first.mkdir()
    second.mkdir()
    granted = [(AccessRoot(path=str(first)),)]
    monkeypatch.setattr(_bash_sessions, "shell_grants", lambda work_dir: granted[0])
    context = ExecutionContext(
        code=f"echo ${_SHELL_MARK}",
        work_dir=str(tmp_path),
        workspace_root=str(tmp_path),
        session_id="replaced",
        timeout=30,
    )
    old = await executor._get_or_create_bash_session(context, {**executor._build_bash_env(None), _SHELL_MARK: "old"})
    try:
        started, gate = tmp_path / "started", tmp_path / "gate"
        running = asyncio.create_task(old.execute(hold_command(started, gate)))
        await wait_until(started.exists)
        queued = asyncio.create_task(executor.execute_bash(context))
        await wait_until(lambda: old._commands_waiting == 1)

        granted[0] = (AccessRoot(path=str(second)),)
        replacement = await executor._get_or_create_bash_session(
            context, {**executor._build_bash_env(None), _SHELL_MARK: "new"}
        )
        async with asyncio.timeout(_SETTLE_S):
            result = await queued
        await running

        assert replacement is not old
        assert result.success, result.error
        assert result.stdout == "new", "the command ran in the shell of the old grants, or in a shell nobody tracks"
        assert len(executor._bash_sessions) == 1
    finally:
        await old.close()


async def test_a_command_the_registry_hands_a_closed_shell_twice_comes_back_as_the_refusal(
    executor: LocalExecutor, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    closed = LocalPersistentSession(make_config(work_dir=str(tmp_path)))
    await closed.close()
    asked: list[str] = []

    async def always_closed(context: ExecutionContext, env: dict[str, str]) -> LocalPersistentSession:
        asked.append("asked")
        return closed

    monkeypatch.setattr(executor, "_get_or_create_bash_session", always_closed)
    context = ExecutionContext(
        code="echo hi", work_dir=str(tmp_path), workspace_root=str(tmp_path), session_id="closed", timeout=30
    )

    try:
        result = await executor.execute_bash(context)

        assert not result.success
        assert result.error == SESSION_CLOSED_BEFORE_START
        assert len(asked) == 2, "the command is handed to the registry once more, not again and again"
    finally:
        await closed.close()


async def test_a_request_stopped_while_its_first_shell_starts_leaves_no_shell(
    executor: LocalExecutor, monkeypatch: pytest.MonkeyPatch
) -> None:
    spawned: list[asyncio.subprocess.Process] = []
    initializing = asyncio.Event()
    create_process = LocalPersistentSession._create_process

    async def recording(self: LocalPersistentSession) -> asyncio.subprocess.Process:
        process = await create_process(self)
        spawned.append(process)
        return process

    async def stalled(self: LocalPersistentSession) -> None:
        initializing.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(LocalPersistentSession, "_create_process", recording)
    monkeypatch.setattr(LocalPersistentSession, "_initialize_shell", stalled)

    env = executor._build_bash_env(None)
    starting = asyncio.create_task(executor._get_or_create_bash_session(_context("stopped"), env))
    await asyncio.wait_for(initializing.wait(), _SETTLE_S)
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting

    assert not executor._bash_sessions, "a session whose shell never finished starting was registered"
    (shell,) = spawned
    try:
        async with asyncio.timeout(_SETTLE_S):
            await shell.wait()
    except TimeoutError:
        shell.kill()
        pytest.fail("the stopped request left its shell running, and no session of the registry would ever close it")
