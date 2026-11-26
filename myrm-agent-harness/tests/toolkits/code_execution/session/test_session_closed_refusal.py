"""A closed session never starts a shell again: a command that reaches it after ``close()`` is refused, not run.

Without the refusal, a command that waits behind the one a closing session ends takes the lock afterwards and starts
a new shell in a session its owner has dropped: outside the registry, under the sandbox the closed shell had. A command
whose shell is already starting when the session closes would be written to a shell that dies under it, so it is
refused once that shell is up. Whoever asks, a closed session spawns no shell: the refusals above guard the two
moments a command can reach it, and the spawn itself guards every other way into a start. These tests use sessions
that run no shell, so each one picks the moment the command gets its turn or counts the spawn.
"""

from __future__ import annotations

import asyncio

from myrm_agent_harness.toolkits.code_execution.session.command_stream import SESSION_CLOSED_BEFORE_START
from myrm_agent_harness.toolkits.code_execution.session.models import SessionExecutionResult, SessionState
from myrm_agent_harness.toolkits.code_execution.session.persistent_session import PersistentSession
from tests.toolkits.code_execution._session_helpers import GatedSession, let_the_loop_run, make_config

_TICKS = 20


class _SlowStartSession(GatedSession):
    """A session whose shell takes as long to start as the test lets it, holding ``_lock`` as a real start does."""

    def __init__(self) -> None:
        super().__init__()
        self.starting = asyncio.Event()
        self.start_may_end = asyncio.Event()

    async def start(self) -> None:
        async with self._lock:
            self.starting.set()
            await self.start_may_end.wait()


class _SpawnCountingSession(PersistentSession):
    """A session with the real ``start`` whose spawn only counts, so a test sees whether a start reached it."""

    def __init__(self) -> None:
        super().__init__(make_config())
        self.spawns = 0

    async def _create_process(self) -> asyncio.subprocess.Process:
        self.spawns += 1
        raise NotImplementedError("a spawn-counting session runs no shell")


async def _command_waiting_behind_a_running_one() -> tuple[
    GatedSession, asyncio.Task[SessionExecutionResult], asyncio.Task[SessionExecutionResult]
]:
    session = GatedSession()
    running = asyncio.create_task(session.execute("hold"))
    await let_the_loop_run(_TICKS)
    waiting = asyncio.create_task(session.execute("later"))
    await let_the_loop_run(_TICKS)
    assert session.ran == ["hold"], "the second command has to be waiting for the shell"
    return session, running, waiting


async def test_a_command_waiting_when_the_session_is_closed_is_refused_instead_of_run() -> None:
    session, running, waiting = await _command_waiting_behind_a_running_one()

    await session.close()
    session.release("hold")
    held, refused = await asyncio.gather(running, waiting)

    assert held.success
    assert not refused.success
    assert refused.error == SESSION_CLOSED_BEFORE_START
    assert session.ran == ["hold"], "the refused command reached the shell"


async def test_a_session_refuses_commands_from_the_moment_close_is_called() -> None:
    session, running, waiting = await _command_waiting_behind_a_running_one()

    async with session._lock:  # a start in progress: close has to wait for it
        closing = asyncio.create_task(session.close())
        await let_the_loop_run(_TICKS)
        session.release("hold")
        refused = await waiting
        assert not closing.done(), "close has to be waiting for the lock when the command gets its turn"
    await closing
    await running

    assert refused.error == SESSION_CLOSED_BEFORE_START
    assert session.ran == ["hold"], "the refused command reached the shell"


async def test_a_command_whose_shell_is_starting_when_the_session_closes_is_refused_instead_of_run() -> None:
    session = _SlowStartSession()
    command = asyncio.create_task(session.execute("later"))
    await session.starting.wait()

    closing = asyncio.create_task(session.close())
    await let_the_loop_run(_TICKS)
    assert not closing.done(), "close has to be waiting for the shell that is starting"
    session.start_may_end.set()
    refused = await command
    await closing

    assert refused.error == SESSION_CLOSED_BEFORE_START
    assert session.ran == [], "the refused command reached the shell"


async def test_a_closed_session_spawns_no_shell_when_it_is_started() -> None:
    session = _SpawnCountingSession()
    await session.close()

    await session.start()

    assert session.spawns == 0, "a closed session spawned a shell that nothing tracks"
    assert session.state == SessionState.TERMINATED, "a start that spawns nothing has no state to move through"
