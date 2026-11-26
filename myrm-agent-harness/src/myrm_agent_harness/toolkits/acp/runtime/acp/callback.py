"""ACP callback handler for the AcpRuntime backend.

Implements the acp.Client callback interface: streams RuntimeEvent instances
through an asyncio.Queue for real-time consumption by AcpRuntime._do_run_turn.
Also answers permission requests (``permission_policy``) and serves the agent's file writes
inside the workspace, reporting every refusal and failure to the agent as a JSON-RPC error.

[INPUT]
- toolkits.acp.types::PermissionBroker, RuntimeConfig, RuntimeEvent, normalize_workspace (POS: ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, serving as the foundation for the entire ACP module.)
- toolkits.acp.runtime.acp.permission_policy::AcpPermissionPolicy (POS: How a turn of an ACP agent is answered when the agent asks to do something)
- acp (POS: ACP SDK; optional extra, imported when a callback runs)

[OUTPUT]
- AcpCallbackHandler: Implements acp.Client callbacks for the ACP runtime.
- client_capabilities: What the host offers the agent in ``initialize``: exactly what the handler serves.

[POS]
ACP callback handler for the AcpRuntime backend. It receives the SDK's own typed objects. A file is written as
bytes, so what the agent writes is what lands on disk (line endings included). It serves no file reads: the
runtime does not offer them, because the agent shares the host's disk and a refusal to read a file that does not
exist yet (the way a new file is told apart from an existing one) is not understood by every agent.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from myrm_agent_harness.core.security.path import is_within_boundary
from myrm_agent_harness.toolkits.acp.runtime.acp.permission_policy import AcpPermissionPolicy
from myrm_agent_harness.toolkits.acp.types import (
    PermissionBroker,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
    create_event,
    normalize_workspace,
)

if TYPE_CHECKING:
    from acp.schema import (
        ClientCapabilities,
        PermissionOption,
        RequestPermissionResponse,
        ToolCallUpdate,
        WriteTextFileResponse,
    )

logger = logging.getLogger(__name__)

# JSON-RPC implementation-defined error: the user (or the absence of anyone to ask) refused the write.
_PERMISSION_DENIED_CODE = -32001


def client_capabilities() -> ClientCapabilities:
    """What the host offers an agent in ``initialize``: the one file operation it governs, a write.

    No file reads. The agent shares the host's disk, so a read offered here is only a second reader that can disagree
    with the agent's own, and not every agent understands its refusal: Gemini (0.71.1) tells a new file from an
    existing one by reading it, takes the error for "not found" for a failure, and then cannot create the file at all.
    """
    from acp.schema import ClientCapabilities, FileSystemCapabilities

    return ClientCapabilities(fs=FileSystemCapabilities(read_text_file=False, write_text_file=True), terminal=False)


def _resolve_safe_path(path_str: str, cwd: str) -> Path | None:
    """Resolve path within cwd, returning None if outside."""
    try:
        candidate = Path(path_str)
        if not candidate.is_absolute():
            candidate = Path(cwd) / candidate
        resolved = candidate.resolve()
        cwd_resolved = Path(cwd).resolve()
        if is_within_boundary(resolved, cwd_resolved):
            return resolved
        return None
    except (ValueError, OSError):
        return None


def _get_attr(obj: object, *keys: str) -> object | None:
    """Extract an attribute from an object or dict by trying multiple keys."""
    for key in keys:
        val: object | None = obj.get(key) if isinstance(obj, dict) else getattr(obj, key, None)
        if val is not None:
            return val
    return None


def _extract_text_content(content: object) -> str | None:
    """Extract text from an ACP content block."""
    if content is None:
        return None
    content_type = _get_attr(content, "type")
    if content_type == "text":
        text = _get_attr(content, "text")
        return str(text) if text is not None else None
    return None


def _tool_output_text(content: object) -> str:
    """The readable text of a tool call's content blocks: its text, and the files its edits touched."""
    if not isinstance(content, Sequence) or isinstance(content, str):
        return ""
    lines: list[str] = []
    for item in content:
        kind = _get_attr(item, "type")
        if kind == "content":
            text = _extract_text_content(_get_attr(item, "content"))
            if text:
                lines.append(text)
        elif kind == "diff":
            lines.append(f"Edited {_get_attr(item, 'path')}")
    return "\n".join(lines)


class AcpCallbackHandler:
    """Implements acp.Client callbacks for the ACP runtime.

    Streams RuntimeEvent instances through an asyncio.Queue for real-time
    consumption by AcpRuntime._do_run_turn. Also collects full response text
    for truncation and final assembly.
    """

    def __init__(self, config: RuntimeConfig, session_id: str, *, agent: str, workspace: str) -> None:
        self._config = config
        self._session_id = session_id
        self._cwd = normalize_workspace(workspace)
        self._policy = AcpPermissionPolicy(config.permission_mode, agent, self._cwd)
        self._text_parts: list[str] = []
        self._event_queue: asyncio.Queue[RuntimeEvent | None] = asyncio.Queue()

    @property
    def session_id(self) -> str:
        return self._session_id

    @session_id.setter
    def session_id(self, value: str) -> None:
        self._session_id = value

    @property
    def event_queue(self) -> asyncio.Queue[RuntimeEvent | None]:
        return self._event_queue

    @property
    def response_text(self) -> str:
        return "".join(self._text_parts)

    def reset(self) -> None:
        self._text_parts.clear()
        while not self._event_queue.empty():
            try:
                self._event_queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    def begin_turn(self, permissions: PermissionBroker | None, session_id: str) -> None:
        """Start the turn ``session_id`` (the host's id for it): ``permissions`` is who is asked while it runs."""
        self.reset()
        self._policy.begin_turn(permissions, session_id)

    def end_turn(self) -> None:
        """End the turn: nobody is asked for what the agent still does after it, and a request that waits is cancelled."""
        self._policy.end_turn()

    def mark_done(self, stop_reason: str) -> None:
        """Signal that the prompt has completed. Pushes a DONE event + sentinel."""
        self._event_queue.put_nowait(create_event(RuntimeEventType.DONE, self._session_id, stop_reason=stop_reason))
        self._event_queue.put_nowait(None)

    def on_connect(self, conn: object) -> None:
        pass

    def _push_event(self, event: RuntimeEvent) -> None:
        """Push a RuntimeEvent to the queue for real-time consumption."""
        self._event_queue.put_nowait(event)

    async def session_update(
        self,
        session_id: str,
        update: object,
        **kwargs: object,
    ) -> None:
        """Convert session_update events to RuntimeEvents and push to queue."""
        update_type = _get_attr(update, "session_update", "sessionUpdate")
        content = _get_attr(update, "content")

        if update_type == "agent_message_chunk":
            text = _extract_text_content(content)
            if text:
                self._text_parts.append(text)
                self._push_event(create_event(RuntimeEventType.TEXT_DELTA, self._session_id, content=text))

        elif update_type == "tool_call":
            title = str(_get_attr(update, "title") or "unknown")
            status = str(_get_attr(update, "status") or "")
            self._text_parts.append(f"\n[tool: {title} ({status})]\n")
            tool_call_id = str(_get_attr(update, "tool_call_id", "toolCallId") or title)
            self._push_event(
                create_event(
                    RuntimeEventType.TOOL_START,
                    self._session_id,
                    tool_name=title,
                    tool_call_id=tool_call_id,
                )
            )

        elif update_type == "tool_call_update":
            status = str(_get_attr(update, "status") or "")
            if status in ("completed", "failed"):
                tool_id = str(_get_attr(update, "tool_call_id", "toolCallId") or "")
                self._text_parts.append(f"[tool update: {tool_id} -> {status}]\n")
                self._push_event(
                    create_event(
                        RuntimeEventType.TOOL_RESULT,
                        self._session_id,
                        tool_call_id=tool_id,
                        output=_tool_output_text(content),
                        is_error=(status == "failed"),
                    )
                )

        elif update_type == "agent_thought_chunk":
            text = _extract_text_content(content)
            if text:
                self._text_parts.append(text)
                self._push_event(create_event(RuntimeEventType.REASONING_DELTA, self._session_id, content=text))

    async def request_permission(
        self,
        session_id: str,
        tool_call: ToolCallUpdate,
        options: list[PermissionOption],
        **kwargs: object,
    ) -> RequestPermissionResponse:
        """Answer the agent's request for permission to run a tool call, by the backend's permission mode."""
        return await self._policy.answer(tool_call, options)

    async def write_text_file(
        self,
        session_id: str,
        path: str,
        content: str,
        **kwargs: object,
    ) -> WriteTextFileResponse:
        """Write a file inside the workspace, byte for byte, once the permission mode allows it."""
        from acp import RequestError
        from acp.schema import WriteTextFileResponse

        resolved = _resolve_safe_path(path, self._cwd)
        if resolved is None:
            logger.warning("acp_file_write_outside_workspace path=%s", path)
            raise RequestError.invalid_params({"path": path, "reason": "the path is outside the workspace"})
        if not await self._policy.allows_write(str(resolved)):
            logger.info("acp_file_write_refused path=%s", path)
            raise RequestError(_PERMISSION_DENIED_CODE, "Permission denied", {"path": path})
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            # Bytes, not ``write_text``: text mode writes ``\n`` as ``\r\n`` on Windows, which would turn a CRLF text
            # into CR CR LF.
            resolved.write_bytes(content.encode("utf-8"))
        except OSError as exc:
            logger.error("acp_file_write_failed path=%s error=%s", path, exc)
            raise RequestError.internal_error({"path": path, "reason": exc.strerror or type(exc).__name__}) from exc
        return WriteTextFileResponse()
