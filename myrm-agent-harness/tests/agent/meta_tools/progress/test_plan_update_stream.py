"""A todo_write call in a running agent reaches the SSE layer as one ``plan_update`` snapshot.

``todo_write`` is a synchronous tool, so the graph runs it in a worker thread. A snapshot written to a channel the
stream executor does not consume, one the shipped middleware stack keeps from the run's stream, or one the dispatcher
does not forward, never reaches the browser, and nothing in the tool itself would notice. These cases run the agent a
run builds (the shipped middleware stack around the real tool) through the real ``StreamExecutor`` over a scripted
model and read the events it emits.

[INPUT]
- agent.meta_tools.progress.todo_write_tool::create_todo_write_tool (POS: the tool under test, not replaced)
- agent.streaming.stream_executor::StreamExecutor (POS: consumes the graph's custom stream and dispatches SSE events)
- tests.support.shipped_graph (POS: the agent a run builds, over a scripted model)
- tests.agent.streaming.scripted_agent (POS: executor harness)
- tests.agent.streaming.custom_chunk (POS: one custom chunk through the real dispatcher)

[OUTPUT]
- Cases: one snapshot per accepted todo_write and none for a rejected one, the snapshot equals the plan file, and the
  dispatcher forwards a snapshot and drops a malformed payload.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from myrm_agent_harness.agent.meta_tools.progress.storage import read_todos_sync_from_workspace
from myrm_agent_harness.agent.meta_tools.progress.todo_write_tool import create_todo_write_tool
from myrm_agent_harness.agent.streaming.types import AgentStreamEvent
from tests.agent.streaming.custom_chunk import dispatch_custom_chunk
from tests.agent.streaming.scripted_agent import TRACKER_PATH, RecordingCompactor, make_context, make_executor
from tests.support.shipped_graph import shipped_agent, shipped_session

PLAN_UPDATE = "plan_update"
FINAL_ANSWER = AIMessage(content="Plan recorded.")
RECURSION_LIMIT = 100


@pytest.fixture(autouse=True)
def _session(tmp_path: Path) -> Iterator[None]:
    with shipped_session(tmp_path):
        yield


def _todo(item_id: str, content: str, status: str) -> dict[str, str]:
    return {"id": item_id, "content": content, "status": status}


def _write(call_id: str, todos: list[dict[str, str]], **arguments: object) -> AIMessage:
    call = {"name": "todo_write", "args": {"todos": todos, **arguments}, "id": call_id}
    return AIMessage(content="", tool_calls=[call])


def _emitted(compactor: object) -> list[AgentStreamEvent]:
    assert isinstance(compactor, RecordingCompactor)
    return [event for event in compactor.events if isinstance(event, AgentStreamEvent)]


async def _events_of_a_run(workspace: Path, script: list[AIMessage]) -> list[AgentStreamEvent]:
    _, agent = shipped_agent(script, [create_todo_write_tool(str(workspace))])
    ctx = make_context(agent, {"messages": [HumanMessage(content="Plan the migration")]})
    ctx.run_config["recursion_limit"] = RECURSION_LIMIT  # every middleware hook of the stack is a step of its own
    executor = make_executor(ctx)
    with patch(TRACKER_PATH, return_value=MagicMock(last_finish_reason=None, usage=None, total_cost_usd=0.0)):
        await executor.execute()
    return _emitted(executor._compactor)


def _plan_updates(events: list[AgentStreamEvent]) -> list[AgentStreamEvent]:
    return [event for event in events if event.type == PLAN_UPDATE]


async def test_each_accepted_todo_write_reaches_the_sse_layer_as_one_plan_update(tmp_path: Path) -> None:
    events = await _events_of_a_run(
        tmp_path,
        [
            _write(
                "w1", [_todo("1", "Read code", "in_progress"), _todo("2", "Apply patch", "pending")], goal="Migrate"
            ),
            _write("w2", [_todo("1", "Read code", "completed"), _todo("2", "Apply patch", "in_progress")], merge=True),
            FINAL_ANSWER,
        ],
    )

    updates = _plan_updates(events)
    assert [update.data["revision"] for update in updates] == [1, 2]
    assert updates[-1].data["goal"] == "Migrate"
    assert [item["status"] for item in updates[-1].data["todos"]] == ["completed", "in_progress"]


async def test_the_last_plan_update_equals_the_plan_file(tmp_path: Path) -> None:
    events = await _events_of_a_run(
        tmp_path, [_write("w1", [_todo("1", "Read code", "pending")], goal="Migrate"), FINAL_ANSWER]
    )

    stored = read_todos_sync_from_workspace(str(tmp_path))
    assert stored is not None
    assert _plan_updates(events)[-1].data == {
        "goal": stored.goal,
        "revision": stored.revision,
        "todos": [item.model_dump() for item in stored.todos],
    }


async def test_a_rejected_todo_write_emits_no_plan_update(tmp_path: Path) -> None:
    events = await _events_of_a_run(tmp_path, [_write("bad", [_todo("1", "Broken", "not-a-status")]), FINAL_ANSWER])

    assert _plan_updates(events) == []
    assert read_todos_sync_from_workspace(str(tmp_path)) is None


async def test_the_dispatcher_forwards_a_plan_snapshot_with_its_message_id() -> None:
    snapshot = {"goal": "Migrate", "revision": 3, "todos": []}

    events = await dispatch_custom_chunk({"name": PLAN_UPDATE, "data": snapshot})

    assert [(event.type, event.data, event.messageId) for event in events] == [(PLAN_UPDATE, snapshot, "msg")]


async def test_the_dispatcher_drops_a_plan_payload_that_is_not_a_snapshot() -> None:
    assert await dispatch_custom_chunk({"name": PLAN_UPDATE, "data": "not a mapping"}) == []
