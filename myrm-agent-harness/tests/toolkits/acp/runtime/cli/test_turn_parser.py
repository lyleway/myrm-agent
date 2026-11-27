"""``TurnParser`` over the output formats of the CLI agents: one line in, at most one event out."""

from __future__ import annotations

import json

from myrm_agent_harness.toolkits.acp.runtime.cli.turn_parser import TurnParser
from myrm_agent_harness.toolkits.acp.types import RuntimeEvent, RuntimeEventType


def _parse(line: str, session_id: str = "s1") -> RuntimeEvent | None:
    events = TurnParser(session_id).feed(line)
    assert len(events) <= 1, events
    return events[0] if events else None


class TestClaudeAndGenericFormats:
    """One line in, at most one event out, for the Claude and generic formats."""

    def test_text_event(self) -> None:
        event = _parse(
            json.dumps({"type": "assistant", "content": "hello"}),
            "s1",
        )
        assert event is not None
        assert event.type == RuntimeEventType.TEXT_DELTA
        assert event.data["content"] == "hello"

    def test_text_event_content_list(self) -> None:
        event = _parse(
            json.dumps({"type": "assistant", "content": [{"type": "text", "text": "hi"}]}),
            "s1",
        )
        assert event is not None
        assert event.type == RuntimeEventType.TEXT_DELTA
        assert event.data["content"] == "hi"

    def test_nested_message_content(self) -> None:
        event = _parse(
            json.dumps(
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "nested"}]},
                }
            ),
            "s1",
        )
        assert event is not None
        assert event.type == RuntimeEventType.TEXT_DELTA
        assert event.data["content"] == "nested"

    def test_tool_use_event(self) -> None:
        event = _parse(
            json.dumps(
                {
                    "type": "tool_use",
                    "name": "bash",
                    "input": {"cmd": "ls"},
                    "id": "tc1",
                }
            ),
            "s1",
        )
        assert event is not None
        assert event.type == RuntimeEventType.TOOL_START
        assert event.data["tool_name"] == "bash"

    def test_tool_result_event(self) -> None:
        event = _parse(
            json.dumps(
                {
                    "type": "tool_result",
                    "tool_use_id": "tc1",
                    "content": "ok",
                    "is_error": False,
                }
            ),
            "s1",
        )
        assert event is not None
        assert event.type == RuntimeEventType.TOOL_RESULT

    def test_result_event_ignored(self) -> None:
        event = _parse(
            json.dumps({"type": "result", "result": "final answer"}),
            "s1",
        )
        assert event is None

    def test_invalid_json_yields_text_delta(self) -> None:
        event = _parse("not json at all", "s1")
        assert event is not None
        assert event.type == RuntimeEventType.TEXT_DELTA
        assert "not json at all" in event.data["content"]

    def test_non_dict_json_yields_text_delta(self) -> None:
        event = _parse(json.dumps([1, 2, 3]), "s1")
        assert event is not None
        assert event.type == RuntimeEventType.TEXT_DELTA

    def test_unknown_type_returns_none(self) -> None:
        event = _parse(
            json.dumps({"type": "unknown_event", "data": "x"}),
            "s1",
        )
        assert event is None

    def test_empty_content_string(self) -> None:
        event = _parse(
            json.dumps({"type": "assistant", "content": ""}),
            "s1",
        )
        assert event is None

    def test_a_line_that_names_no_event_type_is_ignored(self) -> None:
        event = _parse(
            json.dumps({"provider": "aliyun", "model": "MiniMax-M2.5", "sandbox": "read-only"}),
            "s1",
        )
        assert event is None

    def test_a_prompt_echo_is_ignored(self) -> None:
        event = _parse(
            json.dumps({"prompt": "hello"}),
            "s1",
        )
        assert event is None


class TestEdgeCases:
    """Additional NDJSON parsing edge cases."""

    def test_usage_event_carries_the_token_counts(self) -> None:
        event = _parse(
            json.dumps({"type": "usage", "input_tokens": 100, "output_tokens": 50}),
            "s1",
        )
        assert event is not None
        assert event.type == RuntimeEventType.USAGE_UPDATE
        assert (event.data["input_tokens"], event.data["output_tokens"]) == (100, 50)

    def test_text_event_type(self) -> None:
        event = _parse(
            json.dumps({"type": "text", "content": "hello"}),
            "s1",
        )
        assert event is not None
        assert event.type == RuntimeEventType.TEXT_DELTA

    def test_result_event_with_usage(self) -> None:
        event = _parse(
            json.dumps(
                {
                    "type": "result",
                    "usage": {"input_tokens": 1000, "output_tokens": 300},
                }
            ),
            "s1",
        )
        assert event is not None
        assert event.type == RuntimeEventType.USAGE_UPDATE

    def test_thinking_event(self) -> None:
        event = _parse(
            json.dumps({"type": "thinking", "thinking": "hmm let me think"}),
            "s1",
        )
        assert event is not None
        assert event.type == RuntimeEventType.REASONING_DELTA

    def test_ansi_escapes_are_stripped_from_plain_text_lines(self) -> None:
        event = _parse("\x1b[31;1mError:\x1b[0m Connection failed\r\n")
        assert event is not None
        assert event.type == RuntimeEventType.TEXT_DELTA
        assert event.data["content"] == "Error: Connection failed\n"
