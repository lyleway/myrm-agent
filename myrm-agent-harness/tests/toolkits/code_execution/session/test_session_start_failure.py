"""A start that ends before its shell is ready leaves no shell behind.

The registry keeps a session only after ``start()`` returns, so the shell of a start that failed, or whose caller was
cancelled (a request stopped while its first command waits for the shell), belongs to nobody who would close it and
would run until the server exits. A command that finds its shell discarded makes the same start through ``execute``.
These tests spawn real shells, end the start at the point where the shell exists, and read whether it is still
running. The shell of the product runs inside the OS sandbox when there is one and in its own process group when
there is not, and the two are stopped differently, so each case that stops a shell runs in both modes. A command that
waited behind the cancelled start gets a new shell, and a start that cannot spawn a shell fails with its own error.
"""

from __future__ import annotations

import asyncio
import dataclasses
import sys
from collections.abc import Callable, Coroutine
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.code_execution.session import LocalPersistentSession
from myrm_agent_harness.toolkits.code_execution.session.models import SessionState
from tests.support.marked_processes import wait_until
from tests.toolkits.code_execution._session_helpers import let_the_loop_run, make_config

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="drives a real bash shell")

_SETTLE_S = 5.0
_TICKS = 20
_SANDBOX_MODES = ["disable", "auto"]

_Starter = Callable[[LocalPersistentSession], Coroutine[object, object, None]]


async def _start_directly(session: LocalPersistentSession) -> None:
    await session.start()


async def _start_through_a_command(session: LocalPersistentSession) -> None:
    await session.execute("echo the command never reaches a shell")


_STARTERS = [pytest.param(_start_directly, id="start"), pytest.param(_start_through_a_command, id="command")]


class _InitStopsSession(LocalPersistentSession):
    """A real session whose shell is spawned for real and whose initialisation then stalls or fails as the test says."""

    def __init__(self, work_dir: str, sandbox_mode: str) -> None:
        super().__init__(dataclasses.replace(make_config(work_dir=work_dir), sandbox_mode=sandbox_mode))
        self.initializing = asyncio.Event()
        self.init_error: Exception | None = None
        self.init_stalls = True

    async def _initialize_shell(self) -> None:
        self.initializing.set()
        if self.init_error is not None:
            raise self.init_error
        if self.init_stalls:
            await asyncio.Event().wait()
        await super()._initialize_shell()


class _SpawnFailsSession(LocalPersistentSession):
    """A session whose shell binary cannot be executed, so the start ends before any shell exists."""

    async def _create_process(self) -> asyncio.subprocess.Process:
        raise OSError("the shell binary cannot be executed")


async def _cancel_the_start_while_the_shell_initialises(
    session: _InitStopsSession, starter: _Starter
) -> asyncio.subprocess.Process:
    """Start ``session`` with ``starter``, cancel the caller once its shell exists, and return that shell."""
    starting = asyncio.create_task(starter(session))
    await asyncio.wait_for(session.initializing.wait(), _SETTLE_S)
    shell = session.process
    assert shell is not None
    assert shell.returncode is None, "the shell has to be running when the start is cancelled"
    starting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await starting
    return shell


async def _ends_within_settle_time(shell: asyncio.subprocess.Process) -> bool:
    """Whether the shell ends on its own; a shell that is left running keeps this False."""
    try:
        await asyncio.wait_for(shell.wait(), _SETTLE_S)
    except TimeoutError:
        return False
    return True


@pytest.mark.parametrize("starter", _STARTERS)
@pytest.mark.parametrize("sandbox_mode", _SANDBOX_MODES)
async def test_a_start_cancelled_while_its_shell_initialises_leaves_no_shell(
    tmp_path: Path, sandbox_mode: str, starter: _Starter
) -> None:
    session = _InitStopsSession(str(tmp_path), sandbox_mode)
    try:
        shell = await _cancel_the_start_while_the_shell_initialises(session, starter)

        assert await _ends_within_settle_time(shell), "the cancelled start left its shell running"
        assert not session.is_alive
        assert session.state == SessionState.TERMINATED
    finally:
        await session.close()


@pytest.mark.parametrize("starter", _STARTERS)
@pytest.mark.parametrize("sandbox_mode", _SANDBOX_MODES)
async def test_a_start_whose_initialisation_fails_leaves_no_shell(
    tmp_path: Path, sandbox_mode: str, starter: _Starter
) -> None:
    session = _InitStopsSession(str(tmp_path), sandbox_mode)
    session.init_error = RuntimeError("the init batch could not be written")
    try:
        with pytest.raises(RuntimeError, match="init batch"):
            await starter(session)
        shell = session.process
        assert shell is not None

        assert await _ends_within_settle_time(shell), "the failed start left its shell running"
        assert not session.is_alive
        assert session.state == SessionState.TERMINATED
    finally:
        await session.close()


@pytest.mark.parametrize("sandbox_mode", _SANDBOX_MODES)
async def test_a_start_cancelled_again_while_its_shell_is_being_stopped_still_leaves_no_shell(
    tmp_path: Path, sandbox_mode: str
) -> None:
    session = _InitStopsSession(str(tmp_path), sandbox_mode)
    try:
        starting = asyncio.create_task(session.start())
        await asyncio.wait_for(session.initializing.wait(), _SETTLE_S)
        shell = session.process
        assert shell is not None

        starting.cancel()
        await let_the_loop_run(_TICKS)  # the start is now stopping the shell it spawned
        starting.cancel()
        with pytest.raises(asyncio.CancelledError):
            await starting

        assert await _ends_within_settle_time(shell), "the second cancel left the shell running"
    finally:
        await session.close()


async def test_a_start_whose_shell_cannot_be_spawned_fails_with_its_own_error(tmp_path: Path) -> None:
    session = _SpawnFailsSession(dataclasses.replace(make_config(work_dir=str(tmp_path)), sandbox_mode="disable"))
    try:
        with pytest.raises(OSError, match="cannot be executed"):
            await session.start()

        assert session.process is None
        assert session.state == SessionState.TERMINATED
    finally:
        await session.close()


async def test_a_command_queued_behind_a_cancelled_start_runs_in_a_new_shell(tmp_path: Path) -> None:
    session = _InitStopsSession(str(tmp_path), "disable")
    try:
        first = asyncio.create_task(session.execute("echo first"))
        await asyncio.wait_for(session.initializing.wait(), _SETTLE_S)
        cancelled_shell = session.process
        assert cancelled_shell is not None
        queued = asyncio.create_task(session.execute("echo queued"))
        await wait_until(lambda: session._commands_waiting == 1)

        session.init_stalls = False
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        result = await asyncio.wait_for(queued, _SETTLE_S)

        assert result.success
        assert result.stdout.strip() == "queued"
        assert session.process is not cancelled_shell
        assert await _ends_within_settle_time(cancelled_shell), "the cancelled start left its shell running"
    finally:
        await session.close()


async def test_the_next_command_of_a_session_whose_start_was_cancelled_runs_in_a_new_shell(tmp_path: Path) -> None:
    session = _InitStopsSession(str(tmp_path), "disable")
    try:
        cancelled_shell = await _cancel_the_start_while_the_shell_initialises(session, _start_through_a_command)

        session.init_stalls = False
        result = await session.execute("echo recovered")

        assert result.success
        assert result.stdout.strip() == "recovered"
        assert session.process is not cancelled_shell
    finally:
        await session.close()
