"""The events of one Codex turn: the notifications of the app server, told to the host as RuntimeEvents.

[INPUT]
- toolkits.acp.runtime._base::UNAUTHORIZED_RETRIES_LIMIT (POS: Base class for RuntimeBackend implementations)
- toolkits.acp.runtime.codex.failure::is_auth_rejection (POS: Error vocabulary of CodexRuntime)
- toolkits.acp.runtime.codex.items::file_changes, changes_summary (POS: Items that more than one reader needs)
- toolkits.acp.runtime.codex.rpc::Notification (POS: A JSON-RPC peer over the stdio of Codex's app server)
- toolkits.acp.runtime.codex.usage::TokenCounts, UsageMeter (POS: Token usage of a Codex thread, as the step each model request cost)
- toolkits.acp.runtime.codex.wire::Json, as_int, as_list, as_obj, as_str (POS: Typed reads of the decoded JSON that Codex's app server sends)
- toolkits.acp.runtime.event_text::MAX_TOOL_OUTPUT_CHARS, condense_failure_text, tool_summary (POS: Text shaping of the events every runtime emits)
- toolkits.acp.types::RuntimeEvent, RuntimeEventType, create_event (POS: ACP runtime type definitions layer.)

[OUTPUT]
- READ_NOTIFICATIONS: the notification methods a turn reads (the host must never ask the server to leave one out)
- TurnOutcome: how a turn ended, as the server reports it
- TurnTranslator: the stateful translation of one turn's notifications

[POS]
Pure translation: no I/O, so every notification shape is testable from its JSON alone. It speaks for one turn only
(notifications of another turn, a late one of an interrupted turn included, are ignored) and keeps what a stream needs
that a single notification does not tell: that a new message starts after earlier text (and is then set apart by a blank
line, the way a tool call set it apart for the person watching), which items already streamed their text, and how many
rejected-login retries have followed each other.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from myrm_agent_harness.toolkits.acp.runtime._base import UNAUTHORIZED_RETRIES_LIMIT
from myrm_agent_harness.toolkits.acp.runtime.codex.failure import is_auth_rejection
from myrm_agent_harness.toolkits.acp.runtime.codex.items import changes_summary, file_changes
from myrm_agent_harness.toolkits.acp.runtime.codex.rpc import Notification
from myrm_agent_harness.toolkits.acp.runtime.codex.usage import TokenCounts, UsageMeter
from myrm_agent_harness.toolkits.acp.runtime.codex.wire import Json, as_int, as_list, as_obj, as_str
from myrm_agent_harness.toolkits.acp.runtime.event_text import (
    MAX_TOOL_OUTPUT_CHARS,
    condense_failure_text,
    tool_summary,
)
from myrm_agent_harness.toolkits.acp.types import RuntimeEvent, RuntimeEventType, create_event

# What an item that did not run to its end reports as: it failed, or the person (or the policy) refused it.
_NOT_RUN = frozenset({"failed", "declined"})
_BLOCK_BREAK = "\n\n"

# Each notification method a turn reads, and the translator method that reads it.
_READERS: dict[str, str] = {
    "item/started": "_item_started",
    "item/completed": "_item_completed",
    "item/agentMessage/delta": "_message_delta",
    "item/reasoning/summaryTextDelta": "_reasoning_delta",
    "item/reasoning/textDelta": "_reasoning_delta",
    "item/reasoning/summaryPartAdded": "_reasoning_part_added",
    "thread/tokenUsage/updated": "_token_usage",
    "error": "_error",
    "warning": "_warning",
    "mcpServer/startupStatus/updated": "_mcp_startup",
    "turn/completed": "_turn_completed",
}
READ_NOTIFICATIONS = frozenset(_READERS)


@dataclass(frozen=True, slots=True)
class TurnOutcome:
    """How a turn ended: ``status`` is ``completed``, ``interrupted`` or ``failed``; a failed turn carries its error."""

    status: str
    error: Json | None = None


class TurnTranslator:
    """Translates the notifications of one turn; ``outcome`` is set once the server says the turn ended."""

    def __init__(self, session_id: str, thread_id: str, turn_id: str, usage: UsageMeter) -> None:
        self._session_id = session_id
        self._thread_id = thread_id
        self._turn_id = turn_id
        self._usage = usage
        self.outcome: TurnOutcome | None = None
        # The words of the server when it refused the login of this turn again and again; the runtime ends the turn then.
        self.auth_rejection = ""
        self._last_error: Json | None = None
        self._unauthorized_retries = 0
        self._streamed: set[str] = set()
        self._wrote_text = False
        self._break_before_text = False
        self._wrote_reasoning: set[str] = set()
        self._break_before_reasoning = False
        self._handlers: dict[str, Callable[[Json], list[RuntimeEvent]]] = {
            method: getattr(self, reader) for method, reader in _READERS.items()
        }

    def feed(self, notification: Notification) -> list[RuntimeEvent]:
        """The events one notification stands for (none for what the host does not show)."""
        params = notification.params
        thread_id = as_str(params.get("threadId"))
        turn_id = as_str(params.get("turnId")) or as_str(as_obj(params.get("turn")).get("id"))
        if (thread_id and thread_id != self._thread_id) or (turn_id and turn_id != self._turn_id):
            return []
        handler = self._handlers.get(notification.method)
        return handler(params) if handler is not None else []

    # -- text -----------------------------------------------------------------------------------------------

    def _text(self, text: str) -> RuntimeEvent:
        if self._break_before_text:
            text = _BLOCK_BREAK + text
            self._break_before_text = False
        self._wrote_text = True
        return create_event(RuntimeEventType.TEXT_DELTA, self._session_id, content=text)

    def _message_delta(self, params: Json) -> list[RuntimeEvent]:
        delta = as_str(params.get("delta"))
        if not delta:
            return []
        self._unauthorized_retries = 0
        self._streamed.add(as_str(params.get("itemId")))
        return [self._text(delta)]

    def _reasoning(self, item_id: str, text: str) -> RuntimeEvent:
        if self._break_before_reasoning:
            text = _BLOCK_BREAK + text
            self._break_before_reasoning = False
        self._wrote_reasoning.add(item_id)
        return create_event(RuntimeEventType.REASONING_DELTA, self._session_id, content=text)

    def _reasoning_delta(self, params: Json) -> list[RuntimeEvent]:
        delta = as_str(params.get("delta"))
        return [self._reasoning(as_str(params.get("itemId")), delta)] if delta else []

    def _reasoning_part_added(self, params: Json) -> list[RuntimeEvent]:
        """A new section of the summary starts: it is set apart from the one before."""
        self._break_before_reasoning = as_str(params.get("itemId")) in self._wrote_reasoning
        return []

    # -- items ----------------------------------------------------------------------------------------------

    def _tool_start(self, tool_name: str, tool_input: Json, item: Json, summary: str | None = None) -> RuntimeEvent:
        return create_event(
            RuntimeEventType.TOOL_START,
            self._session_id,
            tool_name=tool_name,
            tool_input=dict(tool_input),
            tool_call_id=as_str(item.get("id")),
            summary=tool_summary(tool_input) if summary is None else summary,
        )

    def _tool_result(self, item: Json, output: str, *, is_error: bool) -> RuntimeEvent:
        return create_event(
            RuntimeEventType.TOOL_RESULT,
            self._session_id,
            tool_call_id=as_str(item.get("id")),
            output=output[:MAX_TOOL_OUTPUT_CHARS],
            is_error=is_error,
        )

    def _item_started(self, params: Json) -> list[RuntimeEvent]:
        item = as_obj(params.get("item"))
        self._unauthorized_retries = 0
        match as_str(item.get("type")):
            case "agentMessage":
                self._break_before_text = self._wrote_text
            case "commandExecution":
                return [self._tool_start("command_execution", {"command": as_str(item.get("command"))}, item)]
            case "fileChange":
                changes = file_changes(item)
                tool_input = {"changes": [{"kind": change.kind, "path": change.path} for change in changes]}
                return [self._tool_start("file_change", tool_input, item, changes_summary(changes))]
            case "mcpToolCall":
                name = f"mcp__{as_str(item.get('server'))}__{as_str(item.get('tool'))}"
                return [self._tool_start(name, as_obj(item.get("arguments")), item)]
            case "webSearch":
                return [self._tool_start("web_search", {"query": as_str(item.get("query"))}, item)]
            case "dynamicToolCall":
                return [self._tool_start(as_str(item.get("tool")), as_obj(item.get("arguments")), item)]
        return []

    def _item_completed(self, params: Json) -> list[RuntimeEvent]:
        item = as_obj(params.get("item"))
        item_id = as_str(item.get("id"))
        not_run = as_str(item.get("status")) in _NOT_RUN
        match as_str(item.get("type")):
            case "agentMessage":
                text = as_str(item.get("text"))
                return [self._text(text)] if text and item_id not in self._streamed else []
            case "reasoning":
                if item_id in self._wrote_reasoning:
                    return []
                text = _BLOCK_BREAK.join(
                    part for part in as_list(item.get("summary")) if isinstance(part, str) and part
                )
                return [self._reasoning(item_id, text)] if text else []
            case "commandExecution":
                output = as_str(item.get("aggregatedOutput"))
                return [self._tool_result(item, output, is_error=not_run or as_int(item.get("exitCode")) != 0)]
            case "fileChange":
                return [self._tool_result(item, changes_summary(file_changes(item)), is_error=not_run)]
            case "mcpToolCall":
                failure = as_str(as_obj(item.get("error")).get("message"))
                output = failure or _mcp_text(as_obj(item.get("result")))
                return [self._tool_result(item, output, is_error=not_run or bool(failure))]
            case "webSearch":
                return [self._tool_result(item, "", is_error=False)]
            case "dynamicToolCall":
                return [self._tool_result(item, "", is_error=not_run or item.get("success") is False)]
        return []

    # -- the rest of a turn -----------------------------------------------------------------------------------

    def _token_usage(self, params: Json) -> list[RuntimeEvent]:
        usage = as_obj(params.get("tokenUsage"))
        step = self._usage.step(TokenCounts.of(as_obj(usage.get("total"))), TokenCounts.of(as_obj(usage.get("last"))))
        if step.empty:
            return []
        return [
            create_event(
                RuntimeEventType.USAGE_UPDATE,
                self._session_id,
                input_tokens=step.input,
                output_tokens=step.output,
                cache_read=step.cached,
                cache_write=step.cache_write,
            )
        ]

    def _warning_event(self, message: str) -> list[RuntimeEvent]:
        text = condense_failure_text(message)
        if not text:
            return []
        return [
            create_event(
                RuntimeEventType.STATUS_UPDATE, self._session_id, status="warning", message=text, is_error=False
            )
        ]

    def _error(self, params: Json) -> list[RuntimeEvent]:
        """A failed request: one the server retries is a notice (and, again and again 401, a refused login)."""
        error = as_obj(params.get("error"))
        if params.get("willRetry") is not True:
            self._last_error = error
            self._unauthorized_retries = 0
            return []
        message = condense_failure_text(as_str(error.get("message")))
        if is_auth_rejection(error):
            self._unauthorized_retries += 1
            if self._unauthorized_retries >= UNAUTHORIZED_RETRIES_LIMIT and not self.auth_rejection:
                self.auth_rejection = message or "The login was rejected"
        else:
            self._unauthorized_retries = 0
        return self._warning_event(message)

    def _warning(self, params: Json) -> list[RuntimeEvent]:
        return self._warning_event(as_str(params.get("message")))

    def _mcp_startup(self, params: Json) -> list[RuntimeEvent]:
        """An MCP server the host injected that did not start: the agent runs without its tools, so say so."""
        if as_str(params.get("status")) != "failed":
            return []
        reason = as_str(params.get("error"))
        return self._warning_event(f"{as_str(params.get('name'))}: {reason}" if reason else as_str(params.get("name")))

    def _turn_completed(self, params: Json) -> list[RuntimeEvent]:
        turn = as_obj(params.get("turn"))
        error = as_obj(turn.get("error"))
        self.outcome = TurnOutcome(as_str(turn.get("status")), error or self._last_error)
        return []


def _mcp_text(result: Json) -> str:
    """The text blocks of an MCP tool result, joined."""
    return "".join(as_str(as_obj(block).get("text")) for block in as_list(result.get("content")))
