"""A shell that served a command and waits for the next one ends at once when its session closes.

``close()`` sends SIGTERM and kills the shell only after a grace period. An idle shell is blocked reading its next
command, and bash runs a trap only after that read returns, so a signal trap left installed by the last command
would make every close of a used session wait out the whole grace period. These tests drive a real shell and read
how it ended.
"""

from __future__ import annotations

import signal
import sys
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.code_execution.session import LocalPersistentSession
from tests.toolkits.code_execution._session_helpers import make_config

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="drives a real bash shell")


async def test_closing_an_idle_session_ends_its_shell_with_sigterm(tmp_path: Path) -> None:
    session = LocalPersistentSession(make_config(work_dir=str(tmp_path)))
    await session.start()
    assert (await session.execute("echo ran")).success
    shell = session.process
    assert shell is not None

    await session.close()

    assert shell.returncode == -signal.SIGTERM, "the shell outlived SIGTERM and was killed after the grace period"
