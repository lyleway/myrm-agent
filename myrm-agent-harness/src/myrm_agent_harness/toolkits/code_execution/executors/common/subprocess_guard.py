"""Subprocess timeout guard for code executors.

Wraps the ``communicate()`` of a child (an ``asyncio.subprocess.Process`` or a
``DetachedProcess``) with a timeout and graceful termination (SIGTERM → 2 s
grace → SIGKILL).  All code execution modules that spawn subprocesses (docker
cp, docker exec, venv creation, etc.) should use ``guarded_communicate``
instead of bare ``communicate`` to prevent indefinite hangs.

[INPUT]
- none (imports no other project module)

[OUTPUT]
- (stdout_bytes, stderr_bytes) on success
- SubprocessTimeoutError on timeout (inherits TimeoutError)

[POS]
Single-responsibility guard. Does NOT replace the richer timeout logic
in ``local/executor.py`` which needs process-group kills.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Protocol

logger = logging.getLogger(__name__)


class Child(Protocol):
    """What the guard needs of a child: ``asyncio.subprocess.Process`` and ``DetachedProcess`` both have it."""

    @property
    def pid(self) -> int: ...

    @property
    def returncode(self) -> int | None: ...

    async def communicate(self) -> tuple[bytes, bytes]: ...

    async def wait(self) -> int: ...

    def terminate(self) -> None: ...

    def kill(self) -> None: ...


class SubprocessTimeoutError(TimeoutError):
    """Raised when a guarded subprocess exceeds the allowed timeout."""

    def __init__(self, timeout: float, label: str = "") -> None:
        self.timeout = timeout
        self.label = label
        detail = f" ({label})" if label else ""
        super().__init__(f"Subprocess timed out after {timeout}s{detail}")


async def _terminate_process(proc: Child) -> None:
    """Gracefully terminate: SIGTERM → 2 s wait → SIGKILL."""
    if proc.returncode is not None:
        return
    proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=2.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()


async def guarded_communicate(
    proc: Child,
    timeout: float,
    *,
    label: str = "",
) -> tuple[bytes, bytes]:
    """``communicate()`` with timeout protection and graceful termination.

    Args:
        proc: The subprocess to communicate with.
        timeout: Maximum seconds to wait.
        label: Human-readable tag for log / error messages.

    Returns:
        ``(stdout_bytes, stderr_bytes)`` on success.

    Raises:
        SubprocessTimeoutError: If the subprocess does not finish in time.
    """
    try:
        return await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        logger.warning("Subprocess timeout after %.0fs: %s (pid=%s)", timeout, label or "unknown", proc.pid)
        await _terminate_process(proc)
        raise SubprocessTimeoutError(timeout, label) from None
