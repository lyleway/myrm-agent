"""The Dynamic Workflow lane closes the agent it built when its stream ends, however it ends.

The lane builds a full GeneralAgent for one message and hands its harness agent to the workflow as the parent of the
sub-agents it spawns. The sub-agents share that agent's executor, and the lane never puts the agent in a pool, so the
lane's own end is the only place its executor and shell can be closed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.ai_agents import GeneralAgentParams
from app.services.agent.stream_session.stream_lane_factory import create_dynamic_workflow_stream

_LANE = "app.services.agent.stream_session.stream_lane_factory"
_SETTLE_S = 10

Chunks = AsyncGenerator[dict[str, object], None]


def _params() -> GeneralAgentParams:
    return cast(
        GeneralAgentParams,
        SimpleNamespace(
            message_id="msg-dw",
            chat_id="chat-dw",
            chat_history=None,
            subagent_ids=None,
            incognito_mode=False,
            query="audit the apis",
        ),
    )


async def _two_status_chunks(**_kwargs: object) -> Chunks:
    yield {"type": "status", "step_key": "plan"}
    yield {"type": "status", "step_key": "run"}


@contextmanager
def _lane(workflow: Callable[..., Chunks], *, closing: AsyncMock | None = None) -> Iterator[MagicMock]:
    """The lane with its collaborators replaced; yields the agent wrapper whose close the lane must call."""
    wrapper = MagicMock(chat_id="chat-dw", close=closing or AsyncMock())
    with (
        patch(f"{_LANE}.AgentFactory") as factory,
        patch("app.ai_agents.general_agent.factory.build_general_agent", new=AsyncMock(return_value=object())),
        patch("app.ai_agents.subagent_catalog.DatabaseSubagentCatalog"),
        patch("app.services.budget.enforcer.should_block_execution", new=AsyncMock(return_value=False)),
        patch("app.services.budget.enforcer.reset_session_budget"),
        patch("app.services.context.context_assembly.ContextAssemblyService") as assembly,
        patch("myrm_agent_harness.agent.dynamic_workflow.run_dynamic_workflow_stream", new=workflow),
    ):
        factory.create_general_agent.return_value = wrapper
        assembly.build_facade.return_value.harness_path.return_value = Path("harness")
        yield wrapper


async def test_the_lane_closes_its_agent_after_the_last_chunk() -> None:
    with _lane(_two_status_chunks) as wrapper:
        chunks = []
        async for chunk in create_dynamic_workflow_stream(_params(), None):
            chunks.append(chunk)
            wrapper.close.assert_not_awaited()

    assert len(chunks) == 2
    wrapper.close.assert_awaited_once()


async def test_the_lane_closes_its_agent_when_the_workflow_fails() -> None:
    async def failing(**_kwargs: object) -> Chunks:
        yield {"type": "status", "step_key": "plan"}
        raise RuntimeError("workflow broke")

    with _lane(failing) as wrapper, pytest.raises(RuntimeError, match="workflow broke"):
        async for _ in create_dynamic_workflow_stream(_params(), None):
            pass

    wrapper.close.assert_awaited_once()


async def test_the_lane_closes_its_agent_when_the_consumer_stops_early() -> None:
    with _lane(_two_status_chunks) as wrapper:
        stream = cast(Chunks, create_dynamic_workflow_stream(_params(), None))
        await anext(stream)
        await stream.aclose()

    wrapper.close.assert_awaited_once()


async def test_the_lane_finishes_closing_its_agent_when_its_task_is_cancelled_mid_stream() -> None:
    """A cancelled task is cancelled once: a close that has to wait on the loop is not cut short by it."""
    closed = asyncio.Event()

    async def close_after_a_loop_pass() -> None:
        await asyncio.sleep(0)
        closed.set()

    async def blocked(**_kwargs: object) -> Chunks:
        yield {"type": "status", "step_key": "plan"}
        await asyncio.Event().wait()
        yield {"type": "status", "step_key": "never"}

    with _lane(blocked, closing=AsyncMock(side_effect=close_after_a_loop_pass)) as wrapper:
        streaming = asyncio.Event()

        async def consume() -> None:
            async for _ in create_dynamic_workflow_stream(_params(), None):
                streaming.set()

        task = asyncio.create_task(consume())
        await asyncio.wait_for(streaming.wait(), _SETTLE_S)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    wrapper.close.assert_awaited_once()
    assert closed.is_set()
