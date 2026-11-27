"""Freezes this runtime's own backend so a request to it is taken and never answered, and always thaws it again.

A stopped process still owns its listening socket: the kernel completes the browser's connection and keeps the request
bytes while nobody reads them. To the page that is a backend that hangs, the failure the sync deadlines exist for, not a
backend that is down and refuses the connection; a fake at the page's fetch cannot reproduce the difference.

The signal goes only to the process that listens on the API port and carries this runtime's environment stamp, so the
shared or the user stack can never be frozen. A watchdog in its own session thaws the process after a deadline even when
this process is killed while the backend is frozen.

[INPUT]
- e2e_core.dev_ownership::classify (POS: Positive proof that a process was started by the runtime that is about to act on it)

[OUTPUT]
- owned_backend_pid(api_url): the pid of this runtime's backend, or a refusal that names why it is not safe to signal
- FrozenBackend: the frozen process; `thaw()` resumes it and returns how long it was frozen
- frozen_backend(api_url, ttl_sec=): freezes this runtime's backend for the body of a `with` block and thaws it on every exit

[POS]
Process fault layer of the config-sync deadline E2E: the only code that signals the backend.
"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from urllib.parse import urlsplit

from e2e_core.dev_ownership import classify

RUNTIME_ID_ENV = "MYRM_E2E_RUNTIME_ID"
# Longer than any scenario freezes for: it only matters when this process dies while the backend is frozen.
WATCHDOG_TTL_SEC = 120.0
_LSOF_TIMEOUT_SEC = 5.0


def _listener_pids(port: int) -> list[int]:
    listing = subprocess.run(
        ["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
        capture_output=True,
        text=True,
        timeout=_LSOF_TIMEOUT_SEC,
        check=False,
    )
    return sorted({int(token) for token in listing.stdout.split() if token.isdigit()})


def owned_backend_pid(api_url: str) -> int:
    runtime_id = os.environ.get(RUNTIME_ID_ENV, "").strip()
    if not runtime_id:
        raise RuntimeError(f"REFUSING_TO_FREEZE: {RUNTIME_ID_ENV} is not set, so no isolated runtime owns the backend")
    port = urlsplit(api_url).port
    if port is None:
        raise RuntimeError(f"REFUSING_TO_FREEZE: {api_url!r} names no port")
    pids = _listener_pids(port)
    if len(pids) != 1:
        raise RuntimeError(f"REFUSING_TO_FREEZE: expected one listener on port {port}, found {pids}")
    verdict = classify(pids[0], runtime_id)
    if verdict != "owned":
        raise RuntimeError(
            f"REFUSING_TO_FREEZE: the listener on port {port} (pid {pids[0]}) is {verdict} for runtime {runtime_id!r}"
        )
    return pids[0]


def _start_watchdog(pid: int, ttl_sec: float) -> subprocess.Popen[bytes]:
    """A shell in a session of its own that resumes `pid` after `ttl_sec`, so killing this process cannot strand a frozen one."""
    return subprocess.Popen(
        ["/bin/sh", "-c", f"sleep {ttl_sec:g}; kill -CONT {pid} 2>/dev/null"],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


class FrozenBackend:
    """A process stopped with SIGSTOP; resumed by `thaw()`, or by the watchdog when this process never gets to."""

    def __init__(self, pid: int, *, ttl_sec: float = WATCHDOG_TTL_SEC) -> None:
        self.pid = pid
        # Armed before the signal: a stopped process must never exist without someone due to resume it.
        self._watchdog = _start_watchdog(pid, ttl_sec)
        try:
            os.kill(pid, signal.SIGSTOP)
        except OSError:
            self._disarm_watchdog()
            raise
        self._frozen_at: float | None = time.monotonic()
        print(f"E2E_BACKEND_FREEZE: pid={pid} frozen", flush=True)

    def _disarm_watchdog(self) -> None:
        """Ends the watchdog's whole process group (the shell and the `sleep` it started) unless its deadline already did.

        A group whose shell has ended is gone once the shell is reaped, and until then macOS refuses to signal it.
        """
        with suppress(ProcessLookupError, PermissionError):
            os.killpg(self._watchdog.pid, signal.SIGTERM)
        self._watchdog.wait()

    def thaw(self) -> float:
        """Resumes the process and returns how long it was frozen; 0 when it was already thawed."""
        if self._frozen_at is None:
            return 0.0
        frozen_for = time.monotonic() - self._frozen_at
        self._frozen_at = None
        try:
            os.kill(self.pid, signal.SIGCONT)
        except ProcessLookupError:
            print(f"E2E_BACKEND_FREEZE: pid={self.pid} is gone, nothing to resume", flush=True)
        finally:
            self._disarm_watchdog()
        print(f"E2E_BACKEND_FREEZE: pid={self.pid} thawed after {frozen_for:.1f}s", flush=True)
        return frozen_for


@contextmanager
def frozen_backend(api_url: str, *, ttl_sec: float = WATCHDOG_TTL_SEC) -> Iterator[FrozenBackend]:
    backend = FrozenBackend(owned_backend_pid(api_url), ttl_sec=ttl_sec)
    try:
        yield backend
    finally:
        backend.thaw()
