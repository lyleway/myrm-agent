"""A closed session never starts a shell again: a command that reaches it after ``close()`` is refused, not run.

Without the refusal, a command that waits behind the one a closing session ends takes the lock afterwards and starts
a new shell in a session its owner has dropped: outside the registry, under the sandbox the closed shell had. These
tests use a session that runs no shell, so each one picks the moment the waiting command gets its turn.
"""

from __future__ import annotations

import asyncio

from myrm_agent_harness.toolkits.code_execution.session.command_stream import SESSION_CLOSED_BEFORE_START
from myrm_agent_harness.toolkits.code_execution.session.models import SessionExecutionResult
from tests.toolkits.code_execution._session_helpers import GatedSession, let_the_loop_run

_TICKS = 20


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
