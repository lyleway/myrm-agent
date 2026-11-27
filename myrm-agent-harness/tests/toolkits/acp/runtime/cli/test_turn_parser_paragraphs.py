"""Text before and after a tool call arrives as separate messages that nothing sets apart; the parser does."""

from __future__ import annotations

import json

import pytest

from myrm_agent_harness.toolkits.acp.runtime.cli.turn_parser import TurnParser
from myrm_agent_harness.toolkits.acp.types import RuntimeEvent, RuntimeEventType


def _feed(parser: TurnParser, *lines: dict[str, object]) -> list[RuntimeEvent]:
    return [event for line in lines for event in parser.feed(json.dumps(line))]


def _texts(events: list[RuntimeEvent]) -> list[str]:
    return [str(event.data["content"]) for event in events if event.type == RuntimeEventType.TEXT_DELTA]


def _says(text: str, *, message_id: str = "msg_1", then_calls: str | None = None) -> dict[str, object]:
    blocks: list[dict[str, object]] = [{"type": "text", "text": text}]
    if then_calls:
        blocks.append({"type": "tool_use", "id": "toolu_1", "name": then_calls, "input": {}})
    return {"type": "assistant", "message": {"id": message_id, "content": blocks}}


def _calls(*names: str, message_id: str = "msg_1") -> dict[str, object]:
    blocks = [{"type": "tool_use", "id": f"toolu_{n}", "name": name, "input": {}} for n, name in enumerate(names)]
    return {"type": "assistant", "message": {"id": message_id, "content": blocks}}


_ANSWER: dict[str, object] = {
    "type": "user",
    "message": {"content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "ok"}]},
}


def test_text_after_a_tool_call_starts_a_new_paragraph() -> None:
    parser = TurnParser("s1")

    events = _feed(
        parser,
        _says("I will read the file.", then_calls="Read"),
        _ANSWER,
        _says("The file says hi.", message_id="msg_2"),
    )

    assert _texts(events) == ["I will read the file.", "\n\nThe file says hi."]


def test_a_turn_that_opens_with_a_tool_call_starts_without_a_gap() -> None:
    parser = TurnParser("s1")

    events = _feed(parser, _calls("Read"), _ANSWER, _says("Done.", message_id="msg_2"))

    assert _texts(events) == ["Done."]


@pytest.mark.parametrize(
    ("before", "after"),
    [
        ("Reading.", "Done."),
        ("Reading.\n", "Done."),
        ("Reading.\n\n", "Done."),
        ("Reading.", "\nDone."),
        ("Reading.", "\n\nDone."),
        ("Reading.\n", "\nDone."),
    ],
)
def test_the_blank_line_counts_the_newlines_the_agent_already_wrote(before: str, after: str) -> None:
    parser = TurnParser("s1")

    events = _feed(parser, _says(before, then_calls="Read"), _ANSWER, _says(after, message_id="msg_2"))

    assert "".join(_texts(events)) == "Reading.\n\nDone."


def test_several_tool_calls_in_a_row_add_one_gap() -> None:
    parser = TurnParser("s1")

    events = _feed(
        parser,
        _says("Looking.", then_calls="Glob"),
        _ANSWER,
        _calls("Grep", "Read", message_id="msg_2"),
        _ANSWER,
        _says("Found it.", message_id="msg_3"),
        _says("Really.", message_id="msg_4"),
    )

    assert _texts(events) == ["Looking.", "\n\nFound it.", "Really."]


def test_messages_with_no_tool_call_between_them_are_left_as_they_are() -> None:
    parser = TurnParser("s1")

    events = _feed(parser, _says("One."), _says("Two.", message_id="msg_2"))

    assert _texts(events) == ["One.", "Two."]


def test_the_generic_format_gets_the_same_break() -> None:
    parser = TurnParser("s1")

    events = _feed(
        parser,
        {"type": "text", "content": "Looking."},
        {"type": "tool_use", "name": "bash", "input": {"cmd": "ls"}, "id": "tc1"},
        {"type": "tool_result", "tool_use_id": "tc1", "content": "ok"},
        {"type": "text", "content": "Found it."},
    )

    assert _texts(events) == ["Looking.", "\n\nFound it."]
