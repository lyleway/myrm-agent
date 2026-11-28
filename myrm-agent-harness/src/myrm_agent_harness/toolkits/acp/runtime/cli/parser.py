"""Event builders shared by the CLI output formats.

Each function turns one decoded NDJSON object (or part of it) of a CLI agent into a RuntimeEvent: tool calls and
results, usage, errors and thinking. ``TurnParser`` composes them into the parser of a whole turn; formats that
nest several events in one line (Claude content blocks) are split there.

[INPUT]
- toolkits.acp.runtime.event_text::MAX_TOOL_OUTPUT_CHARS, tool_summary (POS: Text shaping of the events every runtime emits)
- toolkits.acp.types::RuntimeEvent (POS: ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, serving as the foundation for the entire ACP module.)

[OUTPUT]
- parse_json_line: Parse a JSON line, returning None for non-dict values.
- parse_tool_use / parse_tool_result: tool call and result events with a one-line summary / bounded output, marked with
  ``parent_tool_call_id`` when a sub-agent made them
- parse_usage / parse_thinking: usage and reasoning events
- error_text: the raw text of an ``error`` line, whatever shape it has
- extract_text_from_event: text of a flat or nested assistant/text event

[POS]
Shared event builders of the CLI runtime's output parsing.
"""

from __future__ import annotations

import json

from myrm_agent_harness.toolkits.acp.runtime.event_text import MAX_TOOL_OUTPUT_CHARS, tool_summary
from myrm_agent_harness.toolkits.acp.types import RuntimeEvent, RuntimeEventType, create_event


def parse_json_line(line: str) -> dict[str, object] | None:
    """Parse a JSON line, returning None for non-dict values.

    Non-JSON lines are returned as None; the caller should handle them
    as raw text.
    """
    try:
        data = json.loads(line)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _result_text(content: object) -> str:
    """The text of a tool result: a plain string or a list of content blocks; anything else has none."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str)
        )
    return ""


def _parent(parent_tool_call_id: str | None) -> dict[str, object]:
    """The ``parent_tool_call_id`` field of a tool event a sub-agent made; a tool event of the lead agent has none."""
    return {} if parent_tool_call_id is None else {"parent_tool_call_id": parent_tool_call_id}


def parse_tool_use(data: dict[str, object], session_id: str, parent_tool_call_id: str | None = None) -> RuntimeEvent:
    tool_input = data.get("input", {})
    return create_event(
        RuntimeEventType.TOOL_START,
        session_id,
        tool_name=data.get("name", "unknown"),
        tool_input=tool_input,
        tool_call_id=data.get("id", ""),
        summary=tool_summary(tool_input),
        **_parent(parent_tool_call_id),
    )


def parse_tool_result(data: dict[str, object], session_id: str, parent_tool_call_id: str | None = None) -> RuntimeEvent:
    return create_event(
        RuntimeEventType.TOOL_RESULT,
        session_id,
        tool_call_id=data.get("tool_use_id", ""),
        output=_result_text(data.get("content", ""))[:MAX_TOOL_OUTPUT_CHARS],
        is_error=data.get("is_error", False),
        **_parent(parent_tool_call_id),
    )


def parse_usage(data: dict[str, object], session_id: str) -> RuntimeEvent:
    return create_event(
        RuntimeEventType.USAGE_UPDATE,
        session_id,
        input_tokens=data.get("input_tokens", 0),
        output_tokens=data.get("output_tokens", 0),
        cache_read=data.get("cache_read_input_tokens") or 0,
        cache_write=data.get("cache_creation_input_tokens", 0),
    )


def error_text(data: dict[str, object]) -> str:
    """The text of an ``error`` line as the CLI wrote it; empty when the line carries none.

    It is the line's ``error`` object or string, else its own ``message``: some CLIs nest the text under ``error``,
    others put it at the top level of the line.
    """
    error = data.get("error")
    if isinstance(error, dict) and error:
        text = error.get("message") or str(error)
    else:
        text = error or data.get("message")
    return str(text) if text else ""


def parse_thinking(data: dict[str, object], session_id: str) -> RuntimeEvent | None:
    """Parse a thinking/reasoning event. Returns None if no content."""
    content = data.get("thinking", data.get("text", ""))
    if isinstance(content, str) and content:
        return create_event(RuntimeEventType.REASONING_DELTA, session_id, content=content)
    return None


def extract_text_from_event(data: dict[str, object]) -> str | None:
    """Extract text content from an assistant/text event.

    Handles both flat content (string) and nested Claude CLI format
    (message.content[{type: "text", text: "..."}]).
    """
    content = data.get("content", "")
    if not content:
        message = data.get("message")
        if isinstance(message, dict):
            content = message.get("content", "")

    if isinstance(content, str) and content:
        return content

    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str) and text:
                    parts.append(text)
        return "".join(parts) if parts else None

    return None
