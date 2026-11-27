"""A real agent run that ``merge_stream_with_emitter`` advances leaves its tool calls in the run's event log.

The hooks that persist ``tool_start`` and ``tool_end`` fire in the executor task and write to the event logger the
run bound in its first step. The merge steps the stream in tasks of its own, so only a merge that keeps one context
across the steps lets that logger reach the tools. Without it the log holds ``tasks_steps`` only and the tool
analytics (durations, call counts) stay empty. ``test_sse_merge_context.py`` pins the context of the merge with plain generators; this file runs the whole chain,
with only the model scripted: agent loop, tool middleware, hooks, event log.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, AsyncIterator, Iterator
from pathlib import Path

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import tool
from myrm_agent_harness.agent.event_log import FileEventLogBackend
from myrm_agent_harness.agent.middlewares._session_context import clear_active_tools_for_tests
from myrm_agent_harness.agent.security.types import PermissionAction, PermissionRule, SecurityConfig
from myrm_agent_harness.agent.skill_agent import SkillAgent, wait_all_background_tasks
from myrm_agent_harness.agent.types import AgentRuntimeConfig
from myrm_agent_harness.backends.skills.local import LocalSkillBackend

from app.services.agent.streaming_support.sse_failover_emitter import SSEFailoverEmitter, merge_stream_with_emitter
from app.services.agent.streaming_support.stream_collector import StreamContentCollector

_CHAT_ID = "chat_sse_merge_real_run"


class _ScriptedToolCallLLM(FakeMessagesListChatModel):
    def bind_tools(self, tools: object, **kwargs: object) -> _ScriptedToolCallLLM:
        return self


@tool
async def probe_work_tool() -> str:
    """Do work."""
    await asyncio.sleep(0.01)
    return "done"


@pytest.fixture(autouse=True)
def _no_tool_registry_left_behind() -> Iterator[None]:
    yield
    clear_active_tools_for_tests()


async def _as_sse_chunks(events: AsyncGenerator[dict[str, object]]) -> AsyncIterator[str]:
    async for event in events:
        yield f"data: {json.dumps(event, default=str)}\n\n"


@pytest.mark.asyncio
async def test_tool_calls_of_a_run_the_merge_advances_reach_the_event_log_with_their_timing(tmp_path: Path) -> None:
    (tmp_path / "skills").mkdir()
    log_dir = tmp_path / "event_logs"
    agent = SkillAgent(
        llm=_ScriptedToolCallLLM(
            responses=[
                AIMessage(content="", tool_calls=[{"name": "probe_work_tool", "args": {}, "id": "call-1"}]),
                AIMessage(content="finished"),
            ]
        ),
        skill_backend=LocalSkillBackend(tmp_path / "skills"),
        tools=[probe_work_tool],
        enable_memory_auto_extraction=False,
        enable_shell_tools=False,
        config=AgentRuntimeConfig(
            security_config=SecurityConfig(ruleset=(PermissionRule("*", "*", PermissionAction.ALLOW),), yolo_mode_enabled=True)
        ),
        event_log_backend=FileEventLogBackend(log_dir, _CHAT_ID),
    )
    context: dict[str, object] = {
        "workspaces_storage_root": str(tmp_path / "workspaces"),
        "session_id": _CHAT_ID,
        "chat_id": _CHAT_ID,
    }
    emitter = SSEFailoverEmitter(message_id="m", collector=StreamContentCollector())

    try:
        _ = [
            chunk
            async for chunk in merge_stream_with_emitter(_as_sse_chunks(agent.run("use the tool", context=context)), emitter)
        ]
        await wait_all_background_tasks()
    finally:
        await agent.close()

    rows = [json.loads(line) for line in (log_dir / f"{_CHAT_ID}.jsonl").read_text("utf-8").splitlines()]
    tool_events = {row["type"]: row["data"] for row in rows if row["type"] in ("tool_start", "tool_end")}
    assert set(tool_events) == {"tool_start", "tool_end"}
    assert tool_events["tool_end"]["tool_name"] == "probe_work_tool"
    assert tool_events["tool_end"]["status"] == "completed"
    assert tool_events["tool_end"]["duration_ms"] > 0
