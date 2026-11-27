"""Direct delegation: what a turn tells the user about the memory its external agent was given.

An agent that was handed the user's memory and an agent that could not be are told apart, and an agent that was
handed it but never connected is reported once its turn has ended.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass

import pytest
from myrm_agent_harness.toolkits.acp.mcp_servers import HttpMcpServer
from myrm_agent_harness.toolkits.acp.types import (
    MCP_NOT_ATTACHED_STATUS,
    AcpErrorCode,
    RuntimeEvent,
    RuntimeEventType,
    StopReason,
)
from myrm_agent_harness.utils import CancellationToken

from app.services.external_agents import memory_grants
from app.services.external_agents.delegation.stream import SseEvent
from app.services.external_agents.memory_grants import MEMORY_SERVER_NAME, get_memory_grants, memory_server_for
from tests.support.scripted_delegation import (
    CANCELLED,
    MESSAGE_END,
    STEPS,
    ScriptedPool,
    event_types,
    playing,
    scripted_error,
    scripted_event,
    steps_of,
    stream_turn,
)

NOT_CONNECTED = "delegation_claude_mcp_not_connected"
NOT_ATTACHED = "delegation_claude_mcp_not_attached"


class _Owner:
    """Stands for the pool a memory credential belongs to."""


@dataclass(frozen=True)
class _Memory:
    server: HttpMcpServer
    owner: _Owner

    def connect(self) -> None:
        """What the agent does when it opens its connection to the memory endpoint."""
        get_memory_grants().resolve(self.server.headers["Authorization"].removeprefix("Bearer "))


@pytest.fixture
def memory(monkeypatch: pytest.MonkeyPatch) -> Iterator[_Memory]:
    monkeypatch.setattr(memory_grants, "_CONNECT_GRACE_SECONDS", 0.05)
    owner = _Owner()
    yield _Memory(memory_server_for(owner, agent_id="agent-1", external_agent="claude"), owner)
    get_memory_grants().revoke(owner)


def _step_keys(events: list[SseEvent]) -> list[object]:
    return [event["step_key"] for event in events if event["type"] == STEPS]


def _not_attached(*servers: str) -> RuntimeEvent:
    return scripted_event(RuntimeEventType.STATUS_UPDATE, status=MCP_NOT_ATTACHED_STATUS, servers=list(servers))


async def test_an_agent_that_never_connected_to_its_memory_is_reported_once_its_turn_has_ended(memory: _Memory) -> None:
    pool = ScriptedPool(
        playing(scripted_event(RuntimeEventType.TEXT_DELTA, content="done"), scripted_event(RuntimeEventType.DONE)),
        mcp_servers=(memory.server,),
    )

    events = await stream_turn(pool)

    status, notice, end = events[-3:]
    assert (status["step_key"], status["status"]) == ("delegation_claude_status", "completed")
    # The sentence is the frontend's, in the user's language: a title-only, already finished step.
    assert notice == {
        "type": STEPS,
        "step_key": NOT_CONNECTED,
        "tool_name": "delegate:claude",
        "status": "completed",
        "data": [],
    }
    assert end["type"] == MESSAGE_END


async def test_an_agent_that_connected_to_its_memory_has_nothing_to_report(memory: _Memory) -> None:
    memory.connect()
    pool = ScriptedPool(playing(scripted_event(RuntimeEventType.DONE)), mcp_servers=(memory.server,))

    assert _step_keys(await stream_turn(pool)) == ["delegation_claude_status"] * 2


async def test_an_agent_that_connects_during_its_turn_has_nothing_to_report(memory: _Memory) -> None:
    async def connects_midway(_pool: ScriptedPool) -> AsyncGenerator[RuntimeEvent]:
        yield scripted_event(RuntimeEventType.TEXT_DELTA, content="reading your notes")
        memory.connect()
        yield scripted_event(RuntimeEventType.DONE)

    pool = ScriptedPool(connects_midway, mcp_servers=(memory.server,))

    assert _step_keys(await stream_turn(pool)) == ["delegation_claude_status"] * 2


async def test_an_agent_given_no_memory_has_nothing_to_report() -> None:
    pool = ScriptedPool(playing(scripted_event(RuntimeEventType.DONE)))

    assert _step_keys(await stream_turn(pool)) == ["delegation_claude_status"] * 2


async def test_a_memory_the_agent_could_not_be_given_is_said_once_and_not_again_when_the_turn_ends(memory: _Memory) -> None:
    pool = ScriptedPool(
        playing(_not_attached(MEMORY_SERVER_NAME), scripted_event(RuntimeEventType.DONE)),
        mcp_servers=(memory.server,),
    )

    events = await stream_turn(pool)

    assert _step_keys(events) == ["delegation_claude_status", NOT_ATTACHED, "delegation_claude_status"]
    (notice,) = steps_of(events, NOT_ATTACHED)
    assert (notice["status"], notice["data"]) == ("completed", [])


async def test_a_withheld_server_that_is_not_the_memory_says_nothing_about_it() -> None:
    pool = ScriptedPool(playing(_not_attached("some-other-server"), scripted_event(RuntimeEventType.DONE)))

    assert _step_keys(await stream_turn(pool)) == ["delegation_claude_status"] * 3


async def test_an_agent_that_never_connected_to_its_memory_adds_that_to_a_turn_that_hit_its_limit(memory: _Memory) -> None:
    pool = ScriptedPool(
        playing(scripted_event(RuntimeEventType.DONE, stop_reason=StopReason.MAX_TURN_REQUESTS.value)),
        mcp_servers=(memory.server,),
    )

    events = await stream_turn(pool)

    assert _step_keys(events[-4:]) == ["delegation_claude_status", "delegation_claude_turn_limit", NOT_CONNECTED]
    assert events[-1]["type"] == MESSAGE_END


async def test_a_failed_turn_does_not_also_blame_the_memory(memory: _Memory) -> None:
    pool = ScriptedPool(playing(scripted_error(AcpErrorCode.PROCESS_CRASHED, "claude exited")), mcp_servers=(memory.server,))

    events = await stream_turn(pool)

    assert NOT_CONNECTED not in _step_keys(events)
    assert events[-2]["status"] == "error"


async def test_a_failed_turn_is_not_held_up_waiting_for_a_memory_connection(
    memory: _Memory, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The wait for a late connection belongs to a turn that went well; an agent that crashed ends its turn at once.
    monkeypatch.setattr(memory_grants, "_CONNECT_GRACE_SECONDS", 3600.0)
    pool = ScriptedPool(playing(scripted_error(AcpErrorCode.PROCESS_CRASHED, "claude exited")), mcp_servers=(memory.server,))

    async with asyncio.timeout(10):
        events = await stream_turn(pool)

    assert events[-2]["status"] == "error"


async def test_a_stopped_turn_does_not_report_the_memory(memory: _Memory) -> None:
    token = CancellationToken()

    async def stopped(_pool: ScriptedPool) -> AsyncGenerator[RuntimeEvent]:
        yield scripted_event(RuntimeEventType.TEXT_DELTA, content="part")
        token.cancel()

    events = await stream_turn(ScriptedPool(stopped, mcp_servers=(memory.server,)), token)

    assert event_types(events)[-1] == CANCELLED
    assert NOT_CONNECTED not in _step_keys(events)
