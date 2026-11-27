"""Stopping a run whose model message made several tool calls, through the real agent chain.

A scripted model makes a whole batch in one message, in the middleware stack a run ships (conflict gate, tool
interceptor, approvals), driven by the production ``StreamExecutor``. Stop only flips the run's cancel token. Calls
that run together are all cut and the run ends; a call still waiting behind a conflicting one is never started once the
stop has happened, however long the consumer takes to tear the run down: a call the user stopped must not change a file
or reach a remote service just because it was queued.

The tools never look at the stop and stay running far past the tool deadline (60 s), so a run that ends within the
bound was ended by the stop.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware import AgentMiddleware, AgentState, ToolCallRequest
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import BaseTool, StructuredTool
from langgraph.types import Command

from myrm_agent_harness.agent.errors.tool_error_category import ToolErrorCategory
from myrm_agent_harness.agent.middlewares.tooling import tool_executor
from myrm_agent_harness.agent.middlewares.tooling.tool_interceptor_middleware import (
    clear_all_session_loop_guards_for_tests,
)
from myrm_agent_harness.agent.security.tool_registry import TOOL_PERMISSION_MAP, TOOL_SAFETY_METADATA, SafetyMetadata
from myrm_agent_harness.agent.streaming.stream_compactor import StreamCompactor
from myrm_agent_harness.agent.streaming.stream_executor import STREAM_DONE, StreamContext, StreamExecutor
from myrm_agent_harness.agent.types import AgentRunStatistics
from myrm_agent_harness.utils.runtime.cancellation import CancellationToken, CancelReason, set_cancel_token
from tests.support.marked_processes import wait_until
from tests.support.shipped_graph import ScriptedModel, shipped_agent, shipped_session

_WRITE = "stop_write"  # not concurrency-safe: runs alone, behind whatever is running
_READ = "stop_read"  # concurrency-safe: runs together with its peers
_LONG_S = 3600.0
_GRACE_S = 0.5  # production is 5 s; the order of events is under test, not the length of the wait
_SETTLE_S = 30.0
_BOUND_S = 20.0


@dataclass(frozen=True)
class _Call:
    call_id: str
    tool: str
    seconds: float  # how long the call stays running when nothing stops it


class _Probe:
    """What the batch's tools did: the calls that were started, and the ones cut while running."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.cut: list[str] = []

    async def stay(self, tag: str, seconds: float) -> str:
        self.started.append(tag)
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            self.cut.append(tag)
            raise
        return f"done {tag}"

    def tools(self) -> list[BaseTool]:
        return [
            StructuredTool.from_function(coroutine=self.stay, name=_WRITE, description="Change a target, alone."),
            StructuredTool.from_function(coroutine=self.stay, name=_READ, description="Read a source, with peers."),
        ]


class _Answers(AgentMiddleware[AgentState[object], None, object]):
    """In front of the stack: what each call of the batch came back with."""

    def __init__(self) -> None:
        super().__init__()
        self.by_call: dict[str, ToolMessage | Command[object]] = {}

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command[object]]],
    ) -> ToolMessage | Command[object]:
        answer = await handler(request)
        self.by_call[request.tool_call["id"] or ""] = answer
        return answer


class _Consumer:
    """The compactor the executor feeds. After ``stall()`` it takes no further event until ``resume()``: the slowest
    consumer a run can have, a client that stopped reading."""

    def __init__(self) -> None:
        self.events: list[object] = []
        self._stalled = False
        self._resumed = asyncio.Event()

    def stall(self) -> None:
        self._stalled = True

    def resume(self) -> None:
        self._resumed.set()

    async def put(self, event: object) -> None:
        self.events.append(event)
        if self._stalled:
            await self._resumed.wait()

    async def flush(self) -> None:
        pass


class _Run:
    """One scripted run of *batch* under the production StreamExecutor; it is over when the block ends."""

    executing: asyncio.Task[None]

    def __init__(self, batch: Sequence[_Call]) -> None:
        self.probe = _Probe()
        self.answers = _Answers()
        self.consumer = _Consumer()
        self.token = CancellationToken("e2e_stop_batch")
        self.stats = AgentRunStatistics()
        message = AIMessage(
            content="",
            tool_calls=[
                {"id": call.call_id, "name": call.tool, "args": {"tag": call.call_id, "seconds": call.seconds}}
                for call in batch
            ],
        )
        self.model: ScriptedModel
        self.model, agent = shipped_agent(
            [message, AIMessage(content="finished")], self.probe.tools(), ahead=[self.answers]
        )
        ctx = StreamContext(
            agent=agent,
            agent_input={"messages": [HumanMessage(content="go")]},
            merged_context={"locale": "en"},
            run_config={},
            stats=self.stats,
            message_id="e2e_stop_batch",
            cancel_token=self.token,
            steering_token=None,
            source_tracker=MagicMock(),
            output_queue=asyncio.Queue(),
        )
        self.executor = StreamExecutor(
            ctx=ctx, fallback_llm=None, safety_fallback_llm=None, rebuild_agent_fn=MagicMock()
        )
        self.executor._compactor = cast(StreamCompactor, self.consumer)

    async def __aenter__(self) -> _Run:
        set_cancel_token(self.token)  # as the agent runtime does before it spawns the executor task
        self.executing = asyncio.create_task(self.executor.execute())
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        self.consumer.resume()
        self.executing.cancel()
        await asyncio.gather(self.executing, return_exceptions=True)

    def stop(self) -> None:
        self.token.cancel(CancelReason.USER_CANCELLED)

    async def finish(self) -> None:
        await asyncio.wait_for(self.executing, timeout=_BOUND_S)


@pytest.fixture(autouse=True)
def _session(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    clear_all_session_loop_guards_for_tests()
    monkeypatch.setattr(tool_executor, "_STOP_GRACE_S", _GRACE_S)
    monkeypatch.setitem(TOOL_SAFETY_METADATA, _READ, SafetyMetadata(is_read_only=True, is_concurrent_safe=True))
    monkeypatch.setitem(TOOL_SAFETY_METADATA, _WRITE, SafetyMetadata(is_destructive=True))
    # The read-only tool is governed as a built-in read, which runs without an approval card, as the built-in reads do.
    monkeypatch.setitem(TOOL_PERMISSION_MAP, _READ, "file_read")
    with shipped_session(tmp_path):
        yield
    set_cancel_token(None)


async def _until(condition: Callable[[], bool], timeout: float) -> None:
    """Wait for *condition* without failing: the assertion that follows names what did not happen."""
    with contextlib.suppress(TimeoutError):
        await wait_until(condition, timeout)


async def test_a_stop_cuts_every_call_of_a_batch_that_runs_together_and_ends_the_run() -> None:
    batch = [_Call(f"read-{n}", _READ, _LONG_S) for n in range(3)]
    async with _Run(batch) as run:
        await wait_until(lambda: len(run.probe.started) == len(batch), _SETTLE_S)

        run.stop()
        await run.finish()
        await _until(lambda: len(run.probe.cut) == len(batch), timeout=1.0)

        assert sorted(run.probe.cut) == sorted(call.call_id for call in batch), "every running call is cut by the stop"
        assert run.stats.was_cancelled
        assert run.consumer.events[-1] is STREAM_DONE
        assert len(run.model.calls) == 1, "no further model request may follow the stop"


async def test_a_call_waiting_behind_a_stopped_call_is_never_started_however_slow_the_consumer_is() -> None:
    async with _Run([_Call("holder", _WRITE, _LONG_S), _Call("parked", _WRITE, 0.0)]) as run:
        await wait_until(lambda: run.probe.started == ["holder"], _SETTLE_S)

        run.consumer.stall()
        run.stop()
        # The holder is cut once its grace period is over, which lets the parked call through the gate.
        await _until(lambda: "parked" in run.answers.by_call, timeout=_BOUND_S)

        assert run.probe.started == ["holder"], "a call the stop found still waiting must not start afterwards"
        refusal = run.answers.by_call["parked"]
        assert isinstance(refusal, ToolMessage)
        assert refusal.status == "error"
        assert refusal.additional_kwargs["error_category"] == ToolErrorCategory.TOOL_CANCELLED
        assert "was not started" in str(refusal.content)

        run.consumer.resume()
        await run.finish()

        assert run.stats.was_cancelled
        assert run.consumer.events[-1] is STREAM_DONE
        assert len(run.model.calls) == 1


async def test_control_a_batch_nobody_stops_runs_its_waiting_call_after_the_holder() -> None:
    """Without a stop the parked call does start, so the refusal above is what the stop caused."""
    async with _Run([_Call("holder", _WRITE, 0.05), _Call("parked", _WRITE, 0.0)]) as run:
        await run.finish()

        assert run.probe.started == ["holder", "parked"]
        assert not run.stats.was_cancelled
        assert len(run.model.calls) == 2
