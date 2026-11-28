"""How a turn of an ACP agent is answered when the agent asks to do something.

[INPUT]
- toolkits.acp.types::PermissionBroker, PermissionMode, PermissionRequest, PermissionVerdict (POS: ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, serving as the foundation for the entire ACP module.)
- toolkits.acp.runtime.acp.mcp_origin::mcp_server_of (POS: Which MCP server a tool call of an ACP agent belongs to, as the agent says it in the request for permission)
- acp.schema::PermissionOption, ToolCallUpdate, RequestPermissionResponse (POS: ACP SDK wire types; imported lazily because the SDK is an optional extra)

[OUTPUT]
- AcpPermissionPolicy: answers ``session/request_permission`` and decides whether a file write may go ahead

[POS]
Permission policy of the ACP runtime backend. ``allow_all`` answers every request with the agent's own allow option.
``safe`` allows what only reads, and what the agent says is a tool of an MCP server the host declared ``read_only``
for the turn, and puts everything else to the turn's ``PermissionBroker``; with no broker nobody can say yes, so the
answer is a refusal (the runtime's ``TurnPermissions`` already turns a failing broker into one).
An edit a person allowed is remembered by the files it names, so the agent's following ``fs/write_text_file`` of
those files is not asked a second time. A request still waiting for a person when the turn ends or is cancelled is
answered as cancelled, the outcome ACP gives a cancelled turn: an agent waiting for the answer does not end its
prompt on ``session/cancel`` alone, and the person is no longer asked.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Collection, Sequence
from typing import TYPE_CHECKING

from myrm_agent_harness.toolkits.acp.runtime.acp.mcp_origin import mcp_server_of
from myrm_agent_harness.toolkits.acp.types import (
    PermissionBroker,
    PermissionMode,
    PermissionRequest,
    PermissionVerdict,
)

if TYPE_CHECKING:
    from acp.schema import PermissionOption, RequestPermissionResponse, ToolCallUpdate

logger = logging.getLogger(__name__)

# Tool kinds that only look at things; every other kind (and an agent that does not say) is put to a person.
_READ_KINDS = frozenset({"read", "search", "think"})
# Tool kinds that change files; once allowed, the files they name may be written without asking again.
_WRITE_KINDS = frozenset({"edit", "delete", "move"})
_ALLOW_OPTIONS = ("allow_once", "allow_always")
_REFUSE_OPTIONS = ("reject_once", "reject_always")


class AcpPermissionPolicy:
    """The permission decisions of one ACP backend: its mode, its workspace and, per turn, who is asked."""

    def __init__(self, mode: PermissionMode, agent: str, workspace: str) -> None:
        self._mode = mode
        self._agent = agent
        self._workspace = workspace
        self._broker: PermissionBroker | None = None
        self._session_id = ""
        self._read_only_servers: Collection[str] = ()
        self._allowed_files: set[str] = set()
        self._asking: set[asyncio.Future[PermissionVerdict]] = set()

    def begin_turn(
        self, broker: PermissionBroker | None, session_id: str, read_only_servers: Collection[str] = ()
    ) -> None:
        """Start the turn ``session_id`` (the host's id for it): its broker and read-only servers replace the last turn's.

        No file is allowed yet. The tools of ``read_only_servers`` (the MCP servers the host declared ``read_only``
        for this turn) run without asking.
        """
        self._broker = broker
        self._session_id = session_id
        self._read_only_servers = read_only_servers
        self._allowed_files.clear()

    def end_turn(self) -> None:
        """End the turn: a request that still arrives finds nobody to ask, so it is refused; one that waits is cancelled."""
        self.begin_turn(None, "")
        for asking in tuple(self._asking):
            asking.cancel()

    async def answer(self, tool_call: ToolCallUpdate, options: Sequence[PermissionOption]) -> RequestPermissionResponse:
        """The answer to one ``session/request_permission``: one of the agent's own options, or a cancel."""
        kind = tool_call.kind or "other"
        if self._mode == "allow_all" or kind in _READ_KINDS or mcp_server_of(tool_call, self._read_only_servers):
            return _choose(options, _ALLOW_OPTIONS)
        request = self._request(tool_call, kind)
        verdict = await self._ask(request)
        if verdict is None:
            return _cancelled()
        if verdict.allowed and kind in _WRITE_KINDS:
            self._allowed_files.update(request.paths)
        return _choose(options, _ALLOW_OPTIONS if verdict.allowed else _REFUSE_OPTIONS)

    async def allows_write(self, path: str) -> bool:
        """Whether the agent may write ``path`` (absolute, inside the workspace) through ``fs/write_text_file``."""
        if self._mode == "allow_all" or path in self._allowed_files:
            return True
        request = PermissionRequest(
            agent=self._agent,
            session_id=self._session_id,
            tool_name="write_text_file",
            kind="edit",
            title=f"Write {path}",
            input={"path": path},
            paths=(path,),
        )
        verdict = await self._ask(request)
        allowed = verdict is not None and verdict.allowed
        if allowed:
            self._allowed_files.add(path)
        return allowed

    async def _ask(self, request: PermissionRequest) -> PermissionVerdict | None:
        """The turn's broker's verdict, or ``None`` when the turn ended or was cancelled while the request waited."""
        broker = self._broker
        if broker is None:
            logger.info("acp_permission_refused_unattended agent=%s tool=%s", self._agent, request.tool_name)
            return PermissionVerdict(allowed=False)
        asking = asyncio.ensure_future(broker.decide(request))
        self._asking.add(asking)
        try:
            return await asking
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            return None
        finally:
            self._asking.discard(asking)

    def _request(self, tool_call: ToolCallUpdate, kind: str) -> PermissionRequest:
        tool_name = tool_call.name or tool_call.title or "tool"
        raw_input = tool_call.raw_input
        paths = [location.path for location in tool_call.locations or ()]
        paths.extend(
            content.path
            for content in tool_call.content or ()
            if getattr(content, "type", None) == "diff" and isinstance(getattr(content, "path", None), str)
        )
        return PermissionRequest(
            agent=self._agent,
            session_id=self._session_id,
            tool_name=tool_name,
            kind=kind,
            title=tool_call.title or tool_name,
            input=raw_input if isinstance(raw_input, dict) else ({} if raw_input is None else {"input": raw_input}),
            paths=tuple(dict.fromkeys(self._absolute(path) for path in paths)),
            tool_call_id=tool_call.tool_call_id,
        )

    def _absolute(self, path: str) -> str:
        """``path`` as the file's real location, the form the file callbacks compare it in (a link resolved)."""
        return os.path.realpath(path if os.path.isabs(path) else os.path.join(self._workspace, path))


def _choose(options: Sequence[PermissionOption], kinds: tuple[str, ...]) -> RequestPermissionResponse:
    """The agent's first option of the wanted kinds (in the order of ``kinds``), or a cancel when it offers none."""
    from acp.schema import AllowedOutcome, RequestPermissionResponse

    for kind in kinds:
        for option in options:
            if option.kind == kind:
                return RequestPermissionResponse(outcome=AllowedOutcome(option_id=option.option_id))
    logger.warning("acp_permission_no_matching_option wanted=%s offered=%s", kinds, [option.kind for option in options])
    return _cancelled()


def _cancelled() -> RequestPermissionResponse:
    """The answer that selects no option: the request was not decided (the turn was cancelled)."""
    from acp.schema import DeniedOutcome, RequestPermissionResponse

    return RequestPermissionResponse(outcome=DeniedOutcome())
