"""A child process that keeps running when the process that started it exits.

asyncio kills a child its subprocess transport still owns when the transport is closed or collected. On Linux the
closing of the event loop is what drops the last reference to the transport (the child watcher there holds none), so a
process that exits while its child still runs takes the child with it. ``subprocess.Popen`` has no such finalizer.

[INPUT]
- utils.os_compat::get_process_group_kwargs, kill_spawned_group, SIGKILL (POS: Cross-platform process groups)

[OUTPUT]
- DetachedProcess: a child that leads a process group of its own, with the part of ``asyncio.subprocess.Process`` that
  ``guarded_communicate`` uses (``pid``, ``returncode``, ``wait``, ``communicate``, ``terminate``, ``kill``).
- spawn_detached: start one, its output going to files the caller owns.

[POS]
Ownership of a long-running child by code that starts it but must not outlive it: nothing the starter holds stops the
child, and a stop is always asked for by name.
"""

from __future__ import annotations

import asyncio
import signal
import subprocess
from typing import BinaryIO

from myrm_agent_harness.utils.os_compat import SIGKILL, get_process_group_kwargs, kill_spawned_group

_EXIT_POLL_SECONDS = 0.05


class DetachedProcess:
    """A started child: waiting for it never keeps it from running on, and stopping it reaches what it started too."""

    def __init__(self, process: subprocess.Popen[bytes]) -> None:
        self._process = process

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> int | None:
        """The exit code once the child has ended, otherwise ``None``."""
        return self._process.poll()

    async def wait(self) -> int:
        """Wait for the child to end, by polling: a thread blocked in ``waitpid`` would keep ``asyncio.run`` from returning.

        Cancelling the wait leaves the child running.
        """
        while (code := self._process.poll()) is None:
            await asyncio.sleep(_EXIT_POLL_SECONDS)
        return code

    async def communicate(self) -> tuple[bytes, bytes]:
        """Wait for the child to end; its output goes to files the caller owns, so there is nothing to collect."""
        await self.wait()
        return b"", b""

    def terminate(self) -> None:
        self._signal(signal.SIGTERM)

    def kill(self) -> None:
        self._signal(SIGKILL)

    def _signal(self, sig: signal.Signals) -> None:
        # The group is addressed by the child's pid, which is the child's own until it has been reaped; after that the
        # pid may belong to an unrelated process.
        if self._process.poll() is None:
            kill_spawned_group(self._process.pid, sig)


def spawn_detached(*argv: str, stdout: BinaryIO, stderr: BinaryIO) -> DetachedProcess:
    """Start ``argv`` as the leader of a new process group, reading nothing from stdin and writing to the given files.

    A pipe would not do for the output: once the starter is gone nobody reads it, and the child blocks or dies on
    writing to it.
    """
    return DetachedProcess(
        subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **get_process_group_kwargs())
    )
