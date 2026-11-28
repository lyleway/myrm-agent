"""Claude writes a sub-agent's messages into the stream of the turn, marked with the call that started it.

What the lead agent does and says is the turn. What a sub-agent does is shown under the call that started it, and what
it says stays its own: its report comes back as the result of that call. ``test_claude_transcripts.py`` holds what the
real binary wrote; these are the shapes it may write.
"""

from __future__ import annotations

import json

import pytest

from myrm_agent_harness.toolkits.acp.runtime.cli.turn_parser import TurnParser
from myrm_agent_harness.toolkits.acp.types import RuntimeEvent, RuntimeEventType

_STARTER = "toolu_agent"


def _feed(parser: TurnParser, *lines: dict[str, object]) -> list[RuntimeEvent]:
    return [event for line in lines for event in parser.feed(json.dumps(line))]


def _texts(events: list[RuntimeEvent]) -> list[str]:
    return [str(event.data["content"]) for event in events if event.type == RuntimeEventType.TEXT_DELTA]


def _assistant(*blocks: dict[str, object], parent: object = None, message_id: str = "msg_1") -> dict[str, object]:
    return {
        "type": "assistant",
        "message": {"id": message_id, "content": list(blocks)},
        "parent_tool_use_id": parent,
    }


def _say(text: str) -> dict[str, object]:
    return {"type": "text", "text": text}


def _call(tool_id: str, name: str) -> dict[str, object]:
    return {"type": "tool_use", "id": tool_id, "name": name, "input": {}}


def _result(tool_id: str, *, parent: object = None) -> dict[str, object]:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": "ok"}
    return {"type": "user", "message": {"content": [block]}, "parent_tool_use_id": parent}


def test_the_tool_calls_of_a_sub_agent_are_marked_with_the_call_that_started_it() -> None:
    events = _feed(
        TurnParser("s1"),
        _assistant(_call(_STARTER, "Agent")),
        _assistant(_call("toolu_read", "Read"), parent=_STARTER),
        _result("toolu_read", parent=_STARTER),
        _result(_STARTER),
    )

    assert [(event.type, event.data["tool_call_id"], event.data.get("parent_tool_call_id")) for event in events] == [
        (RuntimeEventType.TOOL_START, _STARTER, None),
        (RuntimeEventType.TOOL_START, "toolu_read", _STARTER),
        (RuntimeEventType.TOOL_RESULT, "toolu_read", _STARTER),
        (RuntimeEventType.TOOL_RESULT, _STARTER, None),
    ]


@pytest.mark.parametrize("parent", [None, "", 7, ["toolu_agent"]])
def test_a_message_that_names_no_call_as_its_parent_belongs_to_the_lead_agent(parent: object) -> None:
    events = _feed(
        TurnParser("s1"), _assistant(_call("toolu_1", "Read"), parent=parent), _result("toolu_1", parent=parent)
    )

    assert [event.type for event in events] == [RuntimeEventType.TOOL_START, RuntimeEventType.TOOL_RESULT]
    assert all("parent_tool_call_id" not in event.data for event in events)


def test_what_a_sub_agent_says_is_not_the_answer() -> None:
    events = _feed(
        TurnParser("s1"),
        _assistant(_say("I will ask a helper."), _call(_STARTER, "Agent")),
        _assistant(
            _say("The helper reads the file."), _call("toolu_read", "Read"), parent=_STARTER, message_id="msg_2"
        ),
        _result("toolu_read", parent=_STARTER),
        {"type": "assistant", "content": "The helper found hi.", "parent_tool_use_id": _STARTER},
        _assistant(_say("The helper found hi."), parent=_STARTER, message_id="msg_3"),
        _result(_STARTER),
        _assistant(_say("The note reads hi."), message_id="msg_4"),
    )

    assert _texts(events) == ["I will ask a helper.", "\n\nThe note reads hi."]


def test_what_a_sub_agent_streams_is_not_the_answer_and_does_not_disturb_the_lead_agents_own_message() -> None:
    """The deltas of the lead agent name no message of their own: they belong to the one that started last."""
    parser = TurnParser("s1")
    lead: dict[str, object] = {
        "type": "stream_event",
        "event": {"type": "message_start", "message": {"id": "msg_lead"}},
    }
    helper: dict[str, object] = {
        "type": "stream_event",
        "event": {"type": "message_start", "message": {"id": "msg_helper"}},
        "parent_tool_use_id": _STARTER,
    }
    helper_words: dict[str, object] = {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "helper words"}},
        "api_message_id": "msg_helper",
        "parent_tool_use_id": _STARTER,
    }
    lead_words: dict[str, object] = {
        "type": "stream_event",
        "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "lead words"}},
    }

    events = _feed(
        parser, lead, helper, helper_words, lead_words, _assistant(_say("lead words"), message_id="msg_lead")
    )

    assert _texts(events) == ["lead words"]


def test_the_failure_of_a_sub_agents_request_is_not_the_failure_of_the_turn() -> None:
    parser = TurnParser("s1")

    _feed(
        parser,
        {"type": "assistant", "error": "rate_limit", "parent_tool_use_id": _STARTER},
        {"type": "result", "subtype": "success", "is_error": False, "result": "done"},
    )

    assert parser.result is not None
    assert parser.result.assistant_error == ""
