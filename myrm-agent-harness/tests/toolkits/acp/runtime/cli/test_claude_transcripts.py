"""Claude Code's own output, recorded and replayed through the parser.

The unit tests build the lines by hand. These are the lines the binary wrote (claude 2.1.330, run in stream-json mode
as ``CliRuntime`` runs it, against a local stand-in for the model API that answered with scripted turns), so a change
in the stream format, or in what the parser makes of it, shows here first. The paths in them are normalised to
``/work``; nothing else was edited.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.runtime.cli.failure import classify_failure
from myrm_agent_harness.toolkits.acp.runtime.cli.turn_parser import TurnParser
from myrm_agent_harness.toolkits.acp.types import AcpErrorCode, RuntimeEvent, RuntimeEventType

_TRANSCRIPTS = Path(__file__).parent / "transcripts"


def _replay(name: str) -> tuple[TurnParser, list[RuntimeEvent]]:
    parser = TurnParser("s1")
    events = [
        event for line in (_TRANSCRIPTS / f"{name}.ndjson").read_text().splitlines() for event in parser.feed(line)
    ]
    return parser, events


def _shape(events: list[RuntimeEvent]) -> list[tuple[str, str]]:
    """Each event as its type and the one field that says what it was about."""
    said = {
        RuntimeEventType.TEXT_DELTA: "content",
        RuntimeEventType.TOOL_START: "tool_name",
        RuntimeEventType.TOOL_RESULT: "output",
    }
    return [(event.type.value, str(event.data.get(said.get(event.type, ""), ""))) for event in events]


def _session_of(name: str) -> str:
    first = json.loads((_TRANSCRIPTS / f"{name}.ndjson").read_text().splitlines()[0])
    return str(first["session_id"])


def test_a_plain_turn_streams_its_text_and_ends_without_failure() -> None:
    parser, events = _replay("claude_text")

    assert _shape(events) == [("text_delta", "Hello from the model."), ("usage_update", "")]
    assert parser.take_announced() == _session_of("claude_text")
    assert parser.result is not None and not parser.result.is_error
    assert classify_failure(result=parser.result, returncode=0, stderr="", killed=False) is None


@pytest.mark.parametrize("name", ["claude_tool", "claude_tool_whole_messages"])
def test_what_the_agent_says_after_a_tool_call_is_set_apart_from_what_it_said_before(name: str) -> None:
    """Streamed deltas and whole messages are two shapes of the same turn; both read as two paragraphs."""
    parser, events = _replay(name)

    assert _shape(events) == [
        ("text_delta", "I will read the file."),
        ("tool_start", "Read"),
        ("tool_result", "1\thi\n2\t"),
        ("text_delta", "\n\nThe file says hi."),
        ("usage_update", ""),
    ]
    assert events[1].data["summary"] == "/work/note.txt"
    assert parser.result is not None and parser.result.message == "The file says hi."


def test_what_a_sub_agent_does_is_marked_with_the_call_that_started_it() -> None:
    """Claude marks a sub-agent's messages with the id of the call that started it; its report is that call's result."""
    _, events = _replay("claude_sub_agent")

    tools = [event for event in events if event.type in (RuntimeEventType.TOOL_START, RuntimeEventType.TOOL_RESULT)]
    starter = tools[0].data["tool_call_id"]
    assert [
        (event.type.value, event.data.get("tool_name"), event.data.get("parent_tool_call_id")) for event in tools
    ] == [
        ("tool_start", "Agent", None),
        ("tool_start", "Read", starter),
        ("tool_result", None, starter),
        ("tool_result", None, None),
    ]
    assert tools[3].data["tool_call_id"] == starter
    assert "The helper found: hi." in str(tools[3].data["output"])


def test_the_answer_of_a_turn_with_a_sub_agent_is_what_the_lead_agent_said() -> None:
    parser, events = _replay("claude_sub_agent")

    assert _shape(events)[0] == ("text_delta", "I will ask a helper.")
    assert _shape(events)[-2] == ("text_delta", "\n\nThe helper says the note reads hi.")
    assert [event.type for event in events].count(RuntimeEventType.TEXT_DELTA) == 2
    assert parser.result is not None and parser.result.message == "The helper says the note reads hi."


def test_stopping_at_the_turn_limit_is_no_failure() -> None:
    parser, events = _replay("claude_turn_limit")

    assert [event.type for event in events] == [
        RuntimeEventType.TEXT_DELTA,
        RuntimeEventType.TOOL_START,
        RuntimeEventType.TOOL_RESULT,
        RuntimeEventType.USAGE_UPDATE,
    ]
    assert parser.result is not None
    assert parser.result.is_error and parser.result.hit_turn_limit
    assert parser.result.message == "Reached maximum number of turns (1)"
    assert classify_failure(result=parser.result, returncode=1, stderr="", killed=False) is None


@pytest.mark.parametrize(
    ("name", "said"),
    [
        ("claude_max_tokens", "API Error: Claude's response exceeded the 128000 output token maximum"),
        ("claude_refusal", "API Error: Opus 5.5's safeguards flagged this message"),
    ],
)
def test_a_response_the_api_cut_short_is_an_error_in_the_cli_own_words(name: str, said: str) -> None:
    """Claude retries on its own, then reports the stop as an error; the user is told what it said."""
    parser, _ = _replay(name)

    assert parser.result is not None and not parser.result.hit_turn_limit
    error = classify_failure(result=parser.result, returncode=1, stderr="", killed=False, notice=parser.notice)
    assert error is not None
    assert error.code is AcpErrorCode.UNKNOWN
    assert error.message.startswith(said)
