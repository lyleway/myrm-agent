"""DetachedProcess with real children: it ends with the child, stops a child together with what the child started, and leaves
the child running when nobody waits for it any more."""

from __future__ import annotations

import asyncio
import gc
import os
import signal
import sys
import uuid
from asyncio import base_subprocess
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.code_execution.executors.common import detached_process
from myrm_agent_harness.toolkits.code_execution.executors.common.detached_process import DetachedProcess, spawn_detached
from myrm_agent_harness.toolkits.code_execution.executors.common.subprocess_guard import (
    SubprocessTimeoutError,
    guarded_communicate,
)
from myrm_agent_harness.utils.os_compat import SIGKILL
from tests.support.marked_processes import kill_marked, process_alive, wait_until

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the children and the process groups they are stopped by are POSIX"
)

_SLEEPS = "import time; time.sleep(60)"
_IGNORES_TERMINATE = (
    "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); print('ready', flush=True); time.sleep(60)"
)
# The grandchild carries the marker on its command line and the child does not: the child reads the marker from the environment.
_STARTS_A_GRANDCHILD = (
    "import os, subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)', os.environ['GRANDCHILD_MARKER']])\n"
    "time.sleep(60)\n"
)


@pytest.fixture
def spawn(tmp_path: Path) -> Iterator[Callable[[str], DetachedProcess]]:
    """Start a Python snippet detached, its output in ``tmp_path/out`` and ``tmp_path/err``; whatever still runs is killed afterwards."""
    started: list[DetachedProcess] = []

    def start(code: str) -> DetachedProcess:
        with (tmp_path / "out").open("wb") as out, (tmp_path / "err").open("wb") as err:
            child = spawn_detached(sys.executable, "-c", code, stdout=out, stderr=err)
        started.append(child)
        return child

    yield start
    for child in started:
        child.kill()


@pytest.fixture
def grandchild_marker(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    marker = f"grandchild-{uuid.uuid4().hex}"
    monkeypatch.setenv("GRANDCHILD_MARKER", marker)
    yield marker
    kill_marked(marker)


@pytest.fixture
def stdin_nobody_closes() -> Iterator[None]:
    """File descriptor 0 is a pipe whose writer never writes and never closes: a reader of it blocks until it is killed."""
    read_end, write_end = os.pipe()
    saved = os.dup(0)
    os.dup2(read_end, 0)
    yield
    os.dup2(saved, 0)
    for descriptor in (read_end, write_end, saved):
        os.close(descriptor)


def _subprocess_transports() -> list[base_subprocess.BaseSubprocessTransport]:
    gc.collect()
    return [obj for obj in gc.get_objects() if isinstance(obj, base_subprocess.BaseSubprocessTransport)]


async def test_it_ends_with_the_child_and_the_output_lands_in_the_files(
    spawn: Callable[[str], DetachedProcess], tmp_path: Path
) -> None:
    child = spawn("import sys; print('to out'); print('to err', file=sys.stderr); sys.exit(7)")

    assert await asyncio.wait_for(child.communicate(), timeout=30) == (b"", b"")

    assert child.returncode == 7
    assert (tmp_path / "out").read_text() == "to out\n"
    assert (tmp_path / "err").read_text() == "to err\n"


@pytest.mark.usefixtures("stdin_nobody_closes")
async def test_the_child_reads_nothing_from_the_stdin_of_its_starter(spawn: Callable[[str], DetachedProcess]) -> None:
    child = spawn("import sys; sys.exit(0 if sys.stdin.read() == '' else 1)")

    assert await asyncio.wait_for(child.wait(), timeout=30) == 0


async def test_giving_up_the_wait_leaves_the_child_running(spawn: Callable[[str], DetachedProcess]) -> None:
    child = spawn(_SLEEPS)

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(child.wait(), timeout=0.2)

    assert child.returncode is None


async def test_no_asyncio_transport_owns_the_child(spawn: Callable[[str], DetachedProcess]) -> None:
    """The transport kills the child it owns when it is collected, which on Linux is when the event loop closes."""
    before = _subprocess_transports()

    spawn(_SLEEPS)

    assert _subprocess_transports() == before


async def test_terminate_stops_the_child_and_what_it_started(
    spawn: Callable[[str], DetachedProcess], grandchild_marker: str
) -> None:
    child = spawn(_STARTS_A_GRANDCHILD)
    await wait_until(lambda: process_alive(grandchild_marker))

    child.terminate()

    assert await asyncio.wait_for(child.wait(), timeout=30) == -signal.SIGTERM
    await wait_until(lambda: not process_alive(grandchild_marker))


async def test_kill_stops_a_child_that_ignores_terminate(
    spawn: Callable[[str], DetachedProcess], tmp_path: Path
) -> None:
    child = spawn(_IGNORES_TERMINATE)
    await wait_until(lambda: (tmp_path / "out").read_text() == "ready\n")

    child.terminate()
    await asyncio.sleep(0.3)
    assert child.returncode is None

    child.kill()

    assert await asyncio.wait_for(child.wait(), timeout=30) == -SIGKILL


async def test_a_child_that_has_ended_is_not_signalled_again(
    spawn: Callable[[str], DetachedProcess], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Its pid may belong to an unrelated process by now."""
    child = spawn("pass")
    await asyncio.wait_for(child.wait(), timeout=30)
    signalled: list[tuple[int, int]] = []
    monkeypatch.setattr(detached_process, "kill_spawned_group", lambda pid, sig: signalled.append((pid, sig)))

    child.terminate()
    child.kill()

    assert signalled == []


async def test_the_guard_stops_a_child_that_outlasts_its_deadline_together_with_what_it_started(
    spawn: Callable[[str], DetachedProcess], grandchild_marker: str
) -> None:
    child = spawn(_STARTS_A_GRANDCHILD)
    await wait_until(lambda: process_alive(grandchild_marker))

    with pytest.raises(SubprocessTimeoutError):
        await guarded_communicate(child, 0.2, label="detached process test")

    assert child.returncode == -signal.SIGTERM
    await wait_until(lambda: not process_alive(grandchild_marker))
