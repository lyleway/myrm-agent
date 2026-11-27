"""One ``custom`` stream chunk through the real dispatcher.

A tool writes custom events to the run's StreamWriter and the dispatcher decides which of them reach the UI; tests of
that decision need the chunk dispatched and the emitted events read back, without a graph around it.

[INPUT]
- tests.agent.streaming.scripted_agent (POS: executor harness this helper builds on)

[OUTPUT]
- dispatch_custom_chunk: the SSE events the real dispatcher emits for one custom chunk
"""

from __future__ import annotations

from unittest.mock import MagicMock

from myrm_agent_harness.agent.streaming.types import AgentStreamEvent
from tests.agent.streaming.scripted_agent import RecordingCompactor, make_context, make_executor


async def dispatch_custom_chunk(data: object) -> list[AgentStreamEvent]:
    """The SSE events the real dispatcher emits for one ``custom`` chunk of a run."""
    ctx = make_context(MagicMock(), {})
    executor = make_executor(ctx)
    await executor._dispatch_chunk(("custom", data), ctx, [])
    compactor = executor._compactor
    assert isinstance(compactor, RecordingCompactor)
    return [event for event in compactor.events if isinstance(event, AgentStreamEvent)]
