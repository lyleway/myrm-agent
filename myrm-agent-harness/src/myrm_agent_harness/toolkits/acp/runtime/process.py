"""A spawned agent process — line-wise stdout, a bounded stderr tail, and the only code that stops it.

Every way a run can end (EOF, an error, a cancelled task, an abandoned stream, a timeout) leaves through the
``spawn_managed_process`` context, which stops a process that is still running. Stopping is idempotent and runs to
completion even when the task awaiting it is cancelled again, so a stopped run never leaves an agent behind.

[INPUT]
- utils.os_compat::get_process_group_kwargs, kill_process_group (POS: Unified abstractions for POSIX/Windows differences (file locks, process groups))

[OUTPUT]
- ManagedProcess: a running agent process with ``start_prompt`` / ``send``, ``lines``, ``wait``, ``terminate`` and the facts a run is judged by
- ProcessLaunchError: the agent executable does not exist
- spawn_managed_process: async context manager that spawns a ManagedProcess and stops it on exit

[POS]
Process ownership of every runtime that starts an agent: CliRuntime (one process per turn, fed one prompt) and
CodexRuntime (one resident process, driven line by line). Knows nothing about the agent's output format or sessions.
A one-shot prompt is written to stdin beside the run, not before it: a CLI reads stdin when it is ready (Claude's
stream-json input is read only after its start hooks have run), and a prompt of several MB, an image, waits in the
pipe until then. A run that waited for the write could neither stream nor be stopped in the meantime.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from myrm_agent_harness.utils.os_compat import get_process_group_kwargs, kill_process_group

logger = logging.getLogger(__name__)

# One NDJSON line can carry a whole file (a tool result), far past asyncio's 64 KiB default line limit.
_STDOUT_LINE_LIMIT = 16 * 1024 * 1024
_STDERR_TAIL_BYTES = 8 * 1024
_STDERR_READ_BYTES = 4096
_TERMINATE_GRACE_SECONDS = 5.0
# A grandchild that inherited the pipe can keep stderr open after the agent exited; the tail is enough by then.
_STDERR_DRAIN_SECONDS = 2.0
_EXIT_POLL_SECONDS = 0.05
_KILL = getattr(signal, "SIGKILL", signal.SIGTERM)


def _signal_group(pid: int, sig: int) -> None:
    try:
        kill_process_group(pid, sig)
    except OSError:
        logger.debug("managed_process_signal_failed pid=%d signal=%d", pid, sig, exc_info=True)


class ProcessLaunchError(Exception):
    """The agent executable does not exist, so no process was started."""


class ManagedProcess:
    """One running agent process and its pipes."""

    def __init__(self, process: asyncio.subprocess.Process) -> None:
        self._process = process
        self._stderr_tail = b""
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._termination: asyncio.Future[None] | None = None
        self._killed_by_us = False
        self._prompt_writer: asyncio.Task[None] | None = None

    @property
    def returncode(self) -> int | None:
        return self._process.returncode

    @property
    def killed_by_us(self) -> bool:
        """Whether this runtime stopped the process while it was still running (cancel, timeout, shutdown)."""
        return self._killed_by_us

    @property
    def stderr_text(self) -> str:
        """The last few KiB the process wrote to stderr."""
        return self._stderr_tail.decode("utf-8", errors="replace").strip()

    def start_prompt(self, text: str) -> None:
        """Start writing ``text`` to stdin and return at once; stdin is closed once all of it is written.

        The text is encoded here, so a prompt that cannot be encoded fails the caller. The write runs as a task
        that ``close`` ends: a CLI that is not reading yet holds it in ``drain``, and a process that already left
        is not an error.
        """
        stdin = self._process.stdin
        if stdin is not None:
            self._prompt_writer = asyncio.create_task(self._write_prompt(stdin, text.encode("utf-8") + b"\n"))

    async def _write_prompt(self, stdin: asyncio.StreamWriter, data: bytes) -> None:
        try:
            stdin.write(data)
            await stdin.drain()
            stdin.close()
            await stdin.wait_closed()
        except OSError:
            logger.debug("managed_process_left_before_prompt pid=%d", self._process.pid)
        finally:
            with contextlib.suppress(OSError, RuntimeError):
                stdin.close()

    async def send(self, line: str) -> None:
        """Write ``line`` and a newline to the stdin of a process spawned ``interactive``, and wait until it is flushed.

        A whole line is handed to the transport in one call, so lines sent from different tasks never interleave.
        Raises ``OSError`` when the process is not reading any more (it left, or its stdin was closed).
        """
        stdin = self._process.stdin
        if stdin is None or stdin.is_closing():
            raise BrokenPipeError("The process takes no more input")
        stdin.write(line.encode("utf-8") + b"\n")
        await stdin.drain()

    def close_stdin(self) -> None:
        """End the input of an interactive process: a well-behaved agent finishes what it holds and exits."""
        stdin = self._process.stdin
        if stdin is not None and not stdin.is_closing():
            with contextlib.suppress(OSError, RuntimeError):
                stdin.close()

    async def lines(self, idle_tick: float | None = None) -> AsyncIterator[str | None]:
        """Non-empty stdout lines until the process closes its stdout.

        With ``idle_tick`` a ``None`` is yielded each time that many seconds pass without a line, so a caller can
        show that an agent which is silent (one long tool call) is still running. A tick never loses output: a line
        that is only half written stays in the stream buffer and is returned whole once it is complete.
        """
        stdout = self._process.stdout
        if stdout is None:
            return
        while True:
            try:
                raw = await asyncio.wait_for(stdout.readline(), idle_tick)
            except TimeoutError:
                yield None
                continue
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").strip()
            if line:
                yield line

    async def wait(self) -> int:
        """Wait for the exit code, and for the stderr drain unless a leaked pipe keeps it open."""
        returncode = await self._exit_code()
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(asyncio.shield(self._stderr_task), _STDERR_DRAIN_SECONDS)
        return returncode

    async def _exit_code(self) -> int:
        """The exit code as soon as the transport knows it.

        ``Process.wait()`` resolves only once every pipe is closed, so a grandchild that inherited stderr keeps a
        waiter registered before the exit pending until the grandchild itself ends. The transport's return code
        does not depend on the pipes.
        """
        while (code := self._process.returncode) is None:
            await asyncio.sleep(_EXIT_POLL_SECONDS)
        return code

    async def terminate(self) -> None:
        """Stop the process group: SIGTERM, then SIGKILL after a grace period. Safe to call repeatedly.

        The stop runs as its own task and is shielded from the caller, so cancelling the caller does not leave
        the process half-stopped.
        """
        if self._process.returncode is not None:
            return
        if self._termination is None:
            self._killed_by_us = True
            self._termination = asyncio.ensure_future(self._terminate())
        await asyncio.shield(self._termination)

    async def close(self) -> None:
        """Stop the process if it is still running and release the prompt writer and the stderr drain."""
        try:
            if self._process.returncode is None:
                await self.terminate()
        finally:
            self._stderr_task.cancel()
            writer = self._prompt_writer
            if writer is not None and not writer.done():
                writer.cancel()
                await asyncio.wait({writer})

    async def _terminate(self) -> None:
        pid = self._process.pid
        _signal_group(pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(self._process.wait(), _TERMINATE_GRACE_SECONDS)
            return
        except TimeoutError:
            logger.warning("managed_process_ignored_sigterm pid=%d", pid)
        _signal_group(pid, _KILL)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._process.wait(), _TERMINATE_GRACE_SECONDS)

    async def _drain_stderr(self) -> None:
        stderr = self._process.stderr
        if stderr is None:
            return
        while chunk := await stderr.read(_STDERR_READ_BYTES):
            self._stderr_tail = (self._stderr_tail + chunk)[-_STDERR_TAIL_BYTES:]


@asynccontextmanager
async def spawn_managed_process(
    args: list[str],
    *,
    env: dict[str, str],
    cwd: str,
    stdin_text: str | None = None,
    interactive: bool = False,
) -> AsyncIterator[ManagedProcess]:
    """Spawn ``args`` as its own process group and stop it on exit.

    ``stdin_text`` (the one-shot prompt of a CLI) starts being fed at once and ends the input; ``interactive`` keeps
    stdin open for ``send`` instead. With neither, stdin is empty.
    """
    if interactive and stdin_text is not None:
        raise ValueError("An interactive process takes no initial stdin text")
    try:
        process = await asyncio.create_subprocess_exec(
            *args,
            stdin=asyncio.subprocess.PIPE if interactive or stdin_text is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
            cwd=cwd,
            limit=_STDOUT_LINE_LIMIT,
            **get_process_group_kwargs(),
        )
    except FileNotFoundError as exc:
        # A missing working directory fails the same way; only a missing program means the agent is not installed.
        if not os.path.isdir(cwd):
            raise
        raise ProcessLaunchError(f"CLI executable not found: {args[0]}") from exc
    managed = ManagedProcess(process)
    try:
        if stdin_text is not None:
            managed.start_prompt(stdin_text)
        yield managed
    finally:
        await managed.close()
