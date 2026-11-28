"""A Claude Code turn that hands work to a sub-agent, against the real binary.

The model is scripted (``ScriptedAnthropicApi``): it asks for a helper, the helper reads a file of the workspace and
reports, and the model closes with what the helper said. Everything else is real: the ``claude`` binary starts the
sub-agent, runs its tool and writes the sub-agent's messages into the stream of the turn, marked with the call that
started it. A release that marks them differently, or lets a sub-agent's words into the answer, fails here and not in a
user's chat. ``test_claude_transcripts.py`` replays a recording of the same run through the parser alone.

Run with ``-m integration``. Without the CLI the tests skip, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names it.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.runtime.cli.runtime import CliRuntime
from myrm_agent_harness.toolkits.acp.types import RuntimeConfig, RuntimeEvent, RuntimeEventType
from tests.toolkits.acp._fake_anthropic import (
    Call,
    Reply,
    Say,
    ScriptedAnthropicApi,
    first_user_text,
    tool_names,
    tool_results_in,
)
from tests.toolkits.acp.cli_contract import cli_binary

from ._fakes import errors_of, text_of

pytestmark = pytest.mark.integration

# What starts the helper's conversation, so the script can tell its requests from the turn's own.
TASK = "SUBTASK: read note.txt and report what it says."
LEAD = "I will ask a helper."
HELPER_NOTES = "The helper reads the file."
HELPER_REPORT = "The helper found: hi."
ANSWER = "The helper says the note reads hi."


def script_for(workspace: Path) -> Callable[[Mapping[str, object]], Reply]:
    """The model of the turn: the lead agent asks for a helper, the helper reads ``note.txt``, the lead reports."""

    def script(body: Mapping[str, object]) -> Reply:
        names = tool_names(body)
        finished_a_tool = bool(tool_results_in(body))
        if TASK in first_user_text(body):
            if finished_a_tool:
                return Reply((Say(HELPER_REPORT),))
            return Reply((Say(HELPER_NOTES), Call("Read", {"file_path": str(workspace / "note.txt")})))
        if finished_a_tool:
            return Reply((Say(ANSWER),))
        # The tool is called ``Agent`` in the requests of the release under test and ``Task`` in older ones.
        starter = next((name for name in ("Agent", "Task") if name in names), None)
        if starter is None:
            return Reply((Say("ok"),))
        task = {"description": "Read the note", "prompt": TASK, "subagent_type": "general-purpose"}
        return Reply((Say(LEAD), Call(starter, task)))

    return script


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    (path / "note.txt").write_text("hi\n")
    return path


async def _turn(workspace: Path, home: Path) -> list[RuntimeEvent]:
    binary = cli_binary("claude")
    (home / ".claude").mkdir(parents=True)
    async with ScriptedAnthropicApi(script_for(workspace)) as api:
        config = RuntimeConfig(
            backend_type="cli",
            command=binary,
            auth_mode="api_key",
            env={
                "ANTHROPIC_API_KEY": "sk-ant-api03-fake",
                "ANTHROPIC_BASE_URL": api.base_url,
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                "DISABLE_AUTOUPDATER": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            },
            timeout_seconds=90,
        )
        runtime = CliRuntime("claude", config, workspace=str(workspace))
        return [event async for event in runtime.run_turn("ask a helper to read note.txt", "s1")]


async def test_what_a_sub_agent_does_is_marked_with_the_call_that_started_it(workspace: Path, tmp_path: Path) -> None:
    events = await _turn(workspace, tmp_path / "home")

    assert errors_of(events) == []
    tools = [event for event in events if event.type in (RuntimeEventType.TOOL_START, RuntimeEventType.TOOL_RESULT)]
    starter = tools[0]
    assert (starter.type, starter.data["tool_name"]) == (RuntimeEventType.TOOL_START, "Agent")
    assert "parent_tool_call_id" not in starter.data

    helper_start, helper_result, starter_result = tools[1:]
    assert (helper_start.type, helper_start.data["tool_name"]) == (RuntimeEventType.TOOL_START, "Read")
    assert helper_result.type is RuntimeEventType.TOOL_RESULT
    assert helper_result.data["tool_call_id"] == helper_start.data["tool_call_id"]
    assert helper_start.data["parent_tool_call_id"] == starter.data["tool_call_id"]
    assert helper_result.data["parent_tool_call_id"] == starter.data["tool_call_id"]

    # The report of the helper comes back as the result of the call that started it, which belongs to the lead agent.
    assert starter_result.data["tool_call_id"] == starter.data["tool_call_id"]
    assert "parent_tool_call_id" not in starter_result.data
    assert HELPER_REPORT in str(starter_result.data["output"])


async def test_the_answer_of_the_turn_is_what_the_lead_agent_said_and_not_what_the_helper_did(
    workspace: Path, tmp_path: Path
) -> None:
    events = await _turn(workspace, tmp_path / "home")

    assert errors_of(events) == []
    assert text_of(events) == f"{LEAD}\n\n{ANSWER}"
