"""Consumers of the agent stream that do not render the plan ignore a ``plan_update`` snapshot.

The snapshot is a signal for the plan panel. A snapshot that registered as a tool step in another consumer would show
up as IM progress, as a step of a scheduled run (whose effectful-tool check reads its steps) and in an eval run's tool
trajectory; each case replays one through a consumer and shows that nothing it keeps changes.

[INPUT]
- app.core.channel_bridge.agent_executor.stream_events::iter_channel_stream_progress (POS: IM turn consumer)
- app.core.cron.adapters.agent_runner.stream::_consume_stream (POS: scheduled-run consumer)
- app.core.eval.stream_trace::StreamTrace (POS: eval-run consumer)

[OUTPUT]
- One case per consumer: a plan snapshot leaves its turn, steps and tool trajectory untouched.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from myrm_agent_harness.toolkits.cron.types import (
    CronJob,
    DeliveryConfig,
    JobStatus,
    JobType,
    Schedule,
    ScheduleKind,
)

from app.core.channel_bridge.agent_executor.stream_events import iter_channel_stream_progress
from app.core.channel_bridge.executor_helpers import StreamAccumulator
from app.core.cron.adapters.agent_runner.stream import _consume_stream, _StreamAccumulator
from app.core.eval.stream_trace import StreamTrace

PLAN_UPDATE: dict[str, object] = {
    "type": "plan_update",
    "data": {"goal": "Migrate", "revision": 2, "todos": [{"id": "1", "content": "Read code", "status": "in_progress"}]},
}


async def _stream(*events: dict[str, object]) -> AsyncIterator[dict[str, object]]:
    for event in events:
        yield event


def _agent_streaming(*events: dict[str, object]) -> MagicMock:
    from app.ai_agents.general_agent import GeneralAgent

    agent = MagicMock(spec=GeneralAgent)
    agent.model_cfg = SimpleNamespace(model="test-model")
    agent.process_stream = lambda **_kwargs: _stream(*events)
    return agent


def _job() -> CronJob:
    return CronJob(
        id="cron-1",
        name="Daily report",
        job_type=JobType.AGENT,
        schedule=Schedule(kind=ScheduleKind.CRON, expr="0 9 * * *"),
        status=JobStatus.ACTIVE,
        prompt="Generate the report.",
        delivery=DeliveryConfig(channel="chat"),
    )


@pytest.mark.asyncio
async def test_an_im_turn_shows_no_progress_for_a_plan_snapshot() -> None:
    acc = StreamAccumulator()

    progress = [item async for item in iter_channel_stream_progress(_stream(PLAN_UPDATE), acc, locale="en", session_key="")]

    assert progress == []
    assert acc.tool_steps == []


@pytest.mark.asyncio
async def test_a_scheduled_run_records_no_step_for_a_plan_snapshot() -> None:
    acc = _StreamAccumulator()
    agent = _agent_streaming(PLAN_UPDATE, {"type": "message", "data": "done"})

    result = await _consume_stream(agent, _job(), "Generate the report.", {}, acc, untrusted=False)

    assert acc.progress_steps == []
    assert result.success is True
    assert result.retry_safe is True


def test_an_eval_run_records_no_tool_for_a_plan_snapshot() -> None:
    trace = StreamTrace()

    trace.consume(PLAN_UPDATE)

    assert trace.tools_called == []
    assert trace.tool_call_details == []
