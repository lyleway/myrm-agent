"""``close()`` runs to its end when the caller that awaits it is cancelled.

A close waits for the lock while a start is in progress, and the shell that start creates is the one it has to kill.
A caller cancelled during that wait, such as a request stopped while the registry replaces a shell, must neither leave
that shell running with nobody left to close it nor skip releasing the sandbox the session holds. These tests drive a
real shell and read how it ended.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from myrm_agent_harness.toolkits.code_execution.session import LocalPersistentSession
from tests.toolkits.code_execution._session_helpers import let_the_loop_run, make_config

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="drives a real bash shell")

_TICKS = 20
_SETTLE_S = 5.0


async def _cancel_a_close_that_waits_for_a_start(session: LocalPersistentSession) -> None:
    """Close ``session`` while a start holds its lock, cancel the caller, then let the start end."""
    async with session._lock:  # a start in progress: close has to wait for it
        closing = asyncio.create_task(session.close())
        await let_the_loop_run(_TICKS)
        assert not closing.done(), "close has to be waiting for the lock"
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing


async def test_a_close_whose_caller_is_cancelled_still_ends_the_shell(tmp_path: Path) -> None:
    session = LocalPersistentSession(make_config(work_dir=str(tmp_path)))
    await session.start()
    shell = session.process
    assert shell is not None
    try:
        await _cancel_a_close_that_waits_for_a_start(session)

        await asyncio.wait_for(shell.wait(), _SETTLE_S)
    finally:
        await session.close()

    assert not session.is_alive


async def test_a_close_whose_caller_is_cancelled_still_releases_the_sandbox(tmp_path: Path) -> None:
    session = LocalPersistentSession(make_config(work_dir=str(tmp_path)))
    with patch.object(session, "_sandbox_provider") as sandbox:
        await _cancel_a_close_that_waits_for_a_start(session)
        await let_the_loop_run(_TICKS)

        sandbox.cleanup.assert_called_once_with()
