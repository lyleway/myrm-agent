"""Unit tests: only this runtime's own backend is frozen, a frozen process always comes back, and a refusal names why.

The processes are real (a listener, a sleeper), because what is under test is a signal and its undoing; the verdict on who
owns a process is faked, since that proof belongs to ``e2e_core.dev_ownership``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from urllib.parse import urlsplit

import pytest

from tests.e2e.config_sync_deadline import backend_freeze
from tests.e2e.config_sync_deadline.backend_freeze import (
    RUNTIME_ID_ENV,
    FrozenBackend,
    frozen_backend,
    owned_backend_pid,
)

_LISTENER = (
    "import socket, time\n"
    "server = socket.socket()\n"
    "server.bind(('127.0.0.1', 0))\n"
    "server.listen()\n"
    "print(server.getsockname()[1], flush=True)\n"
    "time.sleep(120)\n"
)
_SLEEPER = "import time\ntime.sleep(120)\n"
_RUNTIME = "isolated-runtime-test"

needs_lsof = pytest.mark.skipif(shutil.which("lsof") is None, reason="lsof finds the process that listens on a port")


def _state(pid: int) -> str:
    """The process state letter `ps` reports; `T` is stopped, empty once the process is gone."""
    return subprocess.run(["ps", "-o", "state=", "-p", str(pid)], capture_output=True, text=True, check=False).stdout.strip()


def _wait_for(condition: Callable[[], bool], *, seconds: float = 8.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.05)
    return condition()


@pytest.fixture
def spawn() -> Iterator[Callable[[str], subprocess.Popen[str]]]:
    """Starts a Python process; every process started is killed at the end, stopped or not."""
    started: list[subprocess.Popen[str]] = []

    def start(script: str) -> subprocess.Popen[str]:
        process = subprocess.Popen([sys.executable, "-c", script], stdout=subprocess.PIPE, text=True)
        started.append(process)
        return process

    yield start
    for process in started:
        process.kill()
        process.wait()


def _listening_port(listener: subprocess.Popen[str]) -> int:
    assert listener.stdout is not None
    return int(listener.stdout.readline())


@needs_lsof
def test_the_pid_listening_on_a_port_is_found(spawn: Callable[[str], subprocess.Popen[str]]) -> None:
    listener = spawn(_LISTENER)
    assert backend_freeze._listener_pids(_listening_port(listener)) == [listener.pid]


def test_the_backend_of_this_runtime_is_the_listener_the_runtime_owns(monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[int, str]] = []

    def classify(pid: int, runtime_id: str) -> str:
        asked.append((pid, runtime_id))
        return "owned"

    monkeypatch.setenv(RUNTIME_ID_ENV, f" {_RUNTIME} ")
    monkeypatch.setattr(backend_freeze, "_listener_pids", lambda port: [4242] if port == 18082 else [])
    monkeypatch.setattr(backend_freeze, "classify", classify)

    assert owned_backend_pid("http://127.0.0.1:18082") == 4242
    assert asked == [(4242, _RUNTIME)]


@pytest.mark.parametrize("runtime_id", [None, "", "  "])
def test_without_a_runtime_id_nothing_is_owned_and_nothing_is_looked_up(
    monkeypatch: pytest.MonkeyPatch, runtime_id: str | None
) -> None:
    if runtime_id is None:
        monkeypatch.delenv(RUNTIME_ID_ENV, raising=False)
    else:
        monkeypatch.setenv(RUNTIME_ID_ENV, runtime_id)
    monkeypatch.setattr(backend_freeze, "_listener_pids", lambda port: pytest.fail("a listener was looked up"))

    with pytest.raises(RuntimeError, match=f"REFUSING_TO_FREEZE: {RUNTIME_ID_ENV} is not set"):
        owned_backend_pid("http://127.0.0.1:18082")


def test_an_api_url_without_a_port_names_no_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(RUNTIME_ID_ENV, _RUNTIME)
    monkeypatch.setattr(backend_freeze, "_listener_pids", lambda port: pytest.fail("a listener was looked up"))

    with pytest.raises(RuntimeError, match="names no port"):
        owned_backend_pid("http://127.0.0.1")


@pytest.mark.parametrize("listeners", [[], [11, 12]])
def test_a_port_without_exactly_one_listener_is_not_frozen(monkeypatch: pytest.MonkeyPatch, listeners: list[int]) -> None:
    monkeypatch.setenv(RUNTIME_ID_ENV, _RUNTIME)
    monkeypatch.setattr(backend_freeze, "_listener_pids", lambda port: listeners)
    monkeypatch.setattr(backend_freeze, "classify", lambda pid, runtime_id: pytest.fail("a process was classified"))

    with pytest.raises(RuntimeError, match=re.escape(f"expected one listener on port 18082, found {listeners}")):
        owned_backend_pid("http://127.0.0.1:18082")


@pytest.mark.parametrize("verdict", ["foreign", "unknown"])
def test_a_listener_the_runtime_does_not_own_is_never_frozen(monkeypatch: pytest.MonkeyPatch, verdict: str) -> None:
    monkeypatch.setenv(RUNTIME_ID_ENV, _RUNTIME)
    monkeypatch.setattr(backend_freeze, "_listener_pids", lambda port: [4242])
    monkeypatch.setattr(backend_freeze, "classify", lambda pid, runtime_id: verdict)

    with pytest.raises(RuntimeError, match=rf"pid 4242\) is {verdict} for runtime '{_RUNTIME}'"):
        owned_backend_pid("http://127.0.0.1:18082")


def test_a_frozen_process_is_stopped_until_it_is_thawed(
    spawn: Callable[[str], subprocess.Popen[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    sleeper = spawn(_SLEEPER)
    assert _wait_for(lambda: _state(sleeper.pid) != "")

    backend = FrozenBackend(sleeper.pid)
    assert _wait_for(lambda: _state(sleeper.pid).startswith("T"))

    frozen_for = backend.thaw()
    assert frozen_for > 0
    assert _wait_for(lambda: not _state(sleeper.pid).startswith("T"))
    assert backend._watchdog.poll() is not None, "the watchdog must not outlive the thaw"
    assert backend.thaw() == 0.0, "a second thaw has nothing to resume"
    output = capsys.readouterr().out
    assert f"E2E_BACKEND_FREEZE: pid={sleeper.pid} frozen" in output
    assert f"E2E_BACKEND_FREEZE: pid={sleeper.pid} thawed after" in output


def test_the_watchdog_thaws_a_process_nobody_thawed_and_a_late_thaw_still_succeeds(
    spawn: Callable[[str], subprocess.Popen[str]],
) -> None:
    sleeper = spawn(_SLEEPER)
    assert _wait_for(lambda: _state(sleeper.pid) != "")

    backend = FrozenBackend(sleeper.pid, ttl_sec=1.0)
    assert _wait_for(lambda: _state(sleeper.pid).startswith("T"))

    assert _wait_for(lambda: not _state(sleeper.pid).startswith("T")), "the watchdog never resumed the process"
    # The shell has ended but nobody has reaped it: the case in which macOS refuses to signal its group.
    assert _wait_for(lambda: _state(backend._watchdog.pid).startswith("Z")), "the watchdog shell never ended"
    backend.thaw()
    assert backend._watchdog.poll() is not None


def test_a_freeze_that_cannot_stop_the_process_disarms_its_watchdog(
    monkeypatch: pytest.MonkeyPatch, spawn: Callable[[str], subprocess.Popen[str]]
) -> None:
    gone = spawn("pass")
    gone.wait()
    watchdogs: list[subprocess.Popen[bytes]] = []
    start_watchdog = backend_freeze._start_watchdog

    def recording_start(pid: int, ttl_sec: float) -> subprocess.Popen[bytes]:
        watchdog = start_watchdog(pid, ttl_sec)
        watchdogs.append(watchdog)
        return watchdog

    monkeypatch.setattr(backend_freeze, "_start_watchdog", recording_start)

    with pytest.raises(ProcessLookupError):
        FrozenBackend(gone.pid)

    assert len(watchdogs) == 1 and watchdogs[0].poll() is not None


def test_a_process_that_died_while_frozen_is_reported_and_does_not_raise(
    spawn: Callable[[str], subprocess.Popen[str]], capsys: pytest.CaptureFixture[str]
) -> None:
    sleeper = spawn(_SLEEPER)
    assert _wait_for(lambda: _state(sleeper.pid) != "")
    backend = FrozenBackend(sleeper.pid)
    sleeper.kill()
    sleeper.wait()

    assert backend.thaw() >= 0
    assert f"E2E_BACKEND_FREEZE: pid={sleeper.pid} is gone, nothing to resume" in capsys.readouterr().out
    assert backend._watchdog.poll() is not None


def test_a_watchdog_that_already_ran_out_is_not_signalled_again(spawn: Callable[[str], subprocess.Popen[str]]) -> None:
    sleeper = spawn(_SLEEPER)
    assert _wait_for(lambda: _state(sleeper.pid) != "")
    backend = FrozenBackend(sleeper.pid, ttl_sec=0.1)
    assert _wait_for(lambda: backend._watchdog.poll() is not None), "the watchdog never ended"

    backend.thaw()

    assert not _state(sleeper.pid).startswith("T")


def test_the_block_freezes_the_backend_of_the_api_url_and_thaws_it_on_every_exit(
    monkeypatch: pytest.MonkeyPatch, spawn: Callable[[str], subprocess.Popen[str]]
) -> None:
    sleeper = spawn(_SLEEPER)
    assert _wait_for(lambda: _state(sleeper.pid) != "")
    urls: list[str] = []

    def owned_backend_pid(api_url: str) -> int:
        urls.append(api_url)
        return sleeper.pid

    monkeypatch.setattr(backend_freeze, "owned_backend_pid", owned_backend_pid)

    with pytest.raises(KeyError), frozen_backend("http://127.0.0.1:18082") as backend:
        assert backend.pid == sleeper.pid
        assert _wait_for(lambda: _state(sleeper.pid).startswith("T"))
        raise KeyError("the scenario failed while the backend was frozen")

    assert urls == ["http://127.0.0.1:18082"]
    assert _wait_for(lambda: not _state(sleeper.pid).startswith("T"))


@needs_lsof
def test_the_real_listener_of_an_owned_port_is_frozen_and_answers_again(
    monkeypatch: pytest.MonkeyPatch, spawn: Callable[[str], subprocess.Popen[str]]
) -> None:
    listener = spawn(_LISTENER)
    api_url = f"http://127.0.0.1:{_listening_port(listener)}"
    assert urlsplit(api_url).port is not None
    monkeypatch.setenv(RUNTIME_ID_ENV, _RUNTIME)
    monkeypatch.setattr(backend_freeze, "classify", lambda pid, runtime_id: "owned" if pid == listener.pid else "foreign")

    with frozen_backend(api_url, ttl_sec=30.0) as backend:
        assert backend.pid == listener.pid
        assert _wait_for(lambda: _state(listener.pid).startswith("T"))

    assert _wait_for(lambda: not _state(listener.pid).startswith("T"))
