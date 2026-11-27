"""The registries that tests would otherwise leave in a state the next test reads."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest
import uvicorn
from myrm_agent_harness.agent.skill_agent import wait_all_background_tasks
from myrm_agent_harness.api import track_background_task
from myrm_agent_harness.api.hooks import configure_background_job_store, get_background_job_store

from tests.support.process_state_isolation import forget_tasks_of_closed_loops


def _task_left_pending_on_a_closed_loop() -> asyncio.Task[None]:
    """What a streamed agent turn leaves behind when the loop of its TestClient closes before its cleanup finished.

    Runs on a worker thread: a loop cannot be driven from inside a running one.
    """
    loop = asyncio.new_event_loop()
    task = loop.create_task(asyncio.sleep(3600))
    loop.run_until_complete(asyncio.sleep(0))
    loop.close()
    task._log_destroy_pending = False  # no loop will ever resume it, so its destruction is not an event
    return task


@pytest.mark.asyncio
async def test_a_drain_waits_for_a_live_task_after_the_tasks_of_closed_loops_were_forgotten() -> None:
    """With the stale task still registered, the drain's ``gather`` raises and returns before the live task is done."""
    stale = await asyncio.to_thread(_task_left_pending_on_a_closed_loop)
    track_background_task(stale)

    async def finish_soon() -> None:
        await asyncio.sleep(0.05)

    live = asyncio.create_task(finish_soon())
    track_background_task(live)

    forget_tasks_of_closed_loops()
    await wait_all_background_tasks(timeout_seconds=5)

    assert live.done()


# The next two tests are a pair: the first leaves the job ledger bound the way a run that started jobs does, and the
# second, which runs right after it, finds the ledger unbound only while the plugin unbinds it after every test.
def test_a_run_that_started_jobs_leaves_the_job_ledger_bound(tmp_path: Path) -> None:
    configure_background_job_store(tmp_path / "background_jobs.db")

    assert get_background_job_store() is not None


def test_the_test_after_it_finds_no_job_ledger_bound() -> None:
    assert get_background_job_store() is None


# Another pair: the first starts uvicorn with its default logging config, which changes its loggers; the second finds
# them as they were before the first ran.
_UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")
_uvicorn_state_before_the_first: list[list[tuple[list[logging.Handler], int, bool]]] = []


def _uvicorn_logger_state() -> list[tuple[list[logging.Handler], int, bool]]:
    loggers = map(logging.getLogger, _UVICORN_LOGGERS)
    return [(logger.handlers[:], logger.level, logger.propagate) for logger in loggers]


def test_starting_uvicorn_with_its_default_logging_changes_its_loggers() -> None:
    before = _uvicorn_logger_state()
    _uvicorn_state_before_the_first.append(before)

    uvicorn.Config("app.main:app")

    assert _uvicorn_logger_state() != before


def test_the_test_after_it_finds_the_uvicorn_loggers_as_they_were() -> None:
    if not _uvicorn_state_before_the_first:
        pytest.skip("the test before it did not run")

    assert _uvicorn_logger_state() == _uvicorn_state_before_the_first[-1]
