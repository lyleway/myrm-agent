"""``close_executor_in_background``: the close runs on the running loop and the caller never waits for it."""

from __future__ import annotations

import asyncio
import gc
import weakref

import pytest

from myrm_agent_harness.toolkits.code_execution.executors.base import CodeExecutor, close_executor_in_background
from myrm_agent_harness.toolkits.code_execution.executors.models import ExecutionContext, ExecutionResult

_SETTLE_S = 10


class _Executor(CodeExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.close_calls = 0
        self.closed = asyncio.Event()

    async def execute(self, context: ExecutionContext) -> ExecutionResult:
        raise NotImplementedError

    async def execute_bash(self, context: ExecutionContext) -> ExecutionResult:
        raise NotImplementedError

    async def close(self) -> None:
        self.close_calls += 1
        self.closed.set()


async def test_the_close_runs_on_the_loop_while_the_caller_carries_on() -> None:
    executor = _Executor()

    close_executor_in_background(executor)
    assert executor.close_calls == 0, "the caller waited for the close"

    async with asyncio.timeout(_SETTLE_S):
        await executor.closed.wait()
    assert executor.close_calls == 1


def test_without_a_running_loop_nothing_is_closed_and_no_close_is_left_unawaited(
    recwarn: pytest.WarningsRecorder,
) -> None:
    executor = _Executor()

    close_executor_in_background(executor)
    gc.collect()

    assert executor.close_calls == 0
    assert not [w for w in recwarn if issubclass(w.category, RuntimeWarning)], "a close coroutine was never awaited"


async def test_a_close_still_in_flight_is_kept_alive_until_it_ends() -> None:
    """The loop keeps only weak references to its tasks, so the one that closes the executor must be held."""

    class _Stuck(_Executor):
        async def close(self) -> None:
            await asyncio.Future()  # nothing but the close task itself ever holds this future

    before = asyncio.all_tasks()
    close_executor_in_background(_Stuck())
    (task,) = asyncio.all_tasks() - before
    reference = weakref.ref(task)
    del task
    await asyncio.sleep(0)  # the close starts and waits on the future

    gc.collect()

    survivor = reference()
    assert survivor is not None, "the close task was collected while it was still closing"
    survivor.cancel()
    await asyncio.gather(survivor, return_exceptions=True)
