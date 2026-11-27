"""Answers the requests of Codex's app server with what the person a turn belongs to decided.

[INPUT]
- toolkits.acp.runtime.codex.items::FileChange, ItemBook, changes_summary, file_changes (POS: Items that more than one reader needs)
- toolkits.acp.runtime.codex.rpc::METHOD_NOT_FOUND, RpcError (POS: A JSON-RPC peer over the stdio of Codex's app server)
- toolkits.acp.runtime.codex.wire::Json, as_obj, as_str (POS: Typed reads of the decoded JSON that Codex's app server sends)
- toolkits.acp.runtime.turn_permissions::TurnPermissions (POS: The permission broker of one turn, as the backend of that turn sees it)
- toolkits.acp.types::PermissionMode, PermissionRequest (POS: ACP runtime type definitions layer.)

[OUTPUT]
- ApprovalResponder: the handler of the server's requests: ``begin_turn``, ``end_turn``, ``handle``

[POS]
The permission seam of CodexRuntime. Codex asks (policy ``on-request``) before an edit, and before a command the agent
says needs more than its read-only sandbox allows; each request becomes a ``PermissionRequest`` for the turn's broker
and the verdict goes back as accept or decline. Declining lets the turn go on (the agent learns the action did not
happen); cancelling ends it, which is what a request still waiting when the turn is stopped gets. Where nobody can
answer (no broker, or ``allow_all`` under which Codex asks for nothing) every request is declined: fail closed.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from myrm_agent_harness.toolkits.acp.runtime.codex.items import FileChange, ItemBook, changes_summary, file_changes
from myrm_agent_harness.toolkits.acp.runtime.codex.rpc import METHOD_NOT_FOUND, RpcError
from myrm_agent_harness.toolkits.acp.runtime.codex.wire import Json, as_obj, as_str
from myrm_agent_harness.toolkits.acp.runtime.turn_permissions import TurnPermissions
from myrm_agent_harness.toolkits.acp.types import PermissionMode, PermissionRequest

logger = logging.getLogger(__name__)

_ACCEPT = "accept"
_DECLINE = "decline"
_CANCEL = "cancel"

_MCP_TOOL_APPROVAL = "mcp_tool_call"
_TITLE_CHARS = 120
# A patch can be a whole file; the person is shown an excerpt of each change, of a bounded number of them.
_DIFF_CHARS = 2000
_MAX_CHANGES = 20


@dataclass(slots=True)
class _Turn:
    session_id: str
    permissions: TurnPermissions | None
    mode: PermissionMode
    # Set once the turn is stopped or over: what is still waiting answers ``cancel``, and no later request is asked.
    ending: bool = False


def _title(label: str, detail: str) -> str:
    """One short line for the person asked: what the tool is, and the first line of what it is about to do."""
    lines = detail.strip().splitlines()
    return f"{label}: {lines[0][:_TITLE_CHARS]}" if lines else label


def _edit_kind(changes: Sequence[FileChange]) -> str:
    """The ACP tool kind of a patch: ``delete`` when it only deletes, ``move`` when it renames, else ``edit``."""
    if changes and all(change.kind == "delete" for change in changes):
        return "delete"
    if any(change.moved_to for change in changes):
        return "move"
    return "edit"


class ApprovalResponder:
    """The handler of the server's requests; one per app-server process, serving the turn that is running."""

    def __init__(self, agent: str, items: ItemBook) -> None:
        self._agent = agent
        self._items = items
        self._turn: _Turn | None = None

    def begin_turn(self, session_id: str, permissions: TurnPermissions | None, mode: PermissionMode) -> None:
        """Requests are now put to ``permissions`` (``None``: nobody can answer them)."""
        self._turn = _Turn(session_id, permissions, mode)

    def end_turn(self) -> None:
        """The turn is stopped or over: a request still waiting is cancelled, and a later one is not asked."""
        turn, self._turn = self._turn, None
        if turn is None:
            return
        turn.ending = True
        if turn.permissions is not None:
            # The person is no longer asked, and the server gets its answer at once: a stopped turn must not wait for it.
            turn.permissions.close()

    async def handle(self, method: str, params: Json) -> Mapping[str, object]:
        """The answer to one request of the server."""
        match method:
            case "item/commandExecution/requestApproval":
                return {"decision": await self._decide(self._command_request(params))}
            case "item/fileChange/requestApproval":
                return {"decision": await self._decide(self._file_change_request(params))}
            case "item/permissions/requestApproval":
                granted = await self._decide(self._permissions_request(params)) == _ACCEPT
                return {"permissions": dict(as_obj(params.get("permissions"))) if granted else {}, "scope": "turn"}
            case "mcpServer/elicitation/request":
                return {"action": await self._elicitation(params), "content": None}
            case "item/tool/requestUserInput":
                # Nobody is at a Codex screen to answer: the agent gets no answers and goes on without them.
                return {"answers": {}}
            case "item/tool/call":
                # The host registers no tools of its own with Codex.
                return {"contentItems": [], "success": False}
        raise RpcError(METHOD_NOT_FOUND, f"Method not supported: {method}")

    # -- the request of each approval -----------------------------------------------------------------------

    def _request(
        self,
        params: Json,
        *,
        tool_name: str,
        kind: str,
        title: str,
        tool_input: Mapping[str, object],
        paths: tuple[str, ...] = (),
    ) -> PermissionRequest:
        turn = self._turn
        return PermissionRequest(
            agent=self._agent,
            session_id=turn.session_id if turn is not None else "",
            tool_name=tool_name,
            kind=kind,
            title=title,
            input=dict(tool_input),
            paths=paths,
            tool_call_id=as_str(params.get("itemId")) or None,
        )

    def _command_request(self, params: Json) -> PermissionRequest:
        command = as_str(params.get("command"))
        tool_input: dict[str, object] = {"command": command}
        for key in ("cwd", "reason"):
            if value := as_str(params.get(key)):
                tool_input[key] = value
        if host := as_str(as_obj(params.get("networkApprovalContext")).get("host")):
            tool_input["network_host"] = host
        return self._request(
            params,
            tool_name="command_execution",
            kind="execute",
            title=_title("command_execution", command),
            tool_input=tool_input,
        )

    def _file_change_request(self, params: Json) -> PermissionRequest:
        item = self._items.get(as_str(params.get("turnId")), as_str(params.get("itemId")))
        changes = file_changes(item)
        tool_input: dict[str, object] = {
            "changes": [
                {"kind": change.kind, "path": change.path, "diff": change.diff[:_DIFF_CHARS]}
                for change in changes[:_MAX_CHANGES]
            ]
        }
        for key, field in (("reason", "reason"), ("grant_root", "grantRoot")):
            if value := as_str(params.get(field)):
                tool_input[key] = value
        return self._request(
            params,
            tool_name="file_change",
            kind=_edit_kind(changes),
            title=_title("file_change", changes_summary(changes)),
            tool_input=tool_input,
            paths=tuple(change.path for change in changes[:_MAX_CHANGES]),
        )

    def _permissions_request(self, params: Json) -> PermissionRequest:
        tool_input: dict[str, object] = {"permissions": dict(as_obj(params.get("permissions")))}
        if reason := as_str(params.get("reason")):
            tool_input["reason"] = reason
        return self._request(
            params,
            tool_name="request_permissions",
            kind="other",
            title=_title("request_permissions", as_str(params.get("reason"))),
            tool_input=tool_input,
        )

    async def _elicitation(self, params: Json) -> str:
        """An MCP server's question: the approval of a tool call is put to the person, any other form is declined."""
        meta = as_obj(params.get("_meta"))
        if as_str(meta.get("codex_approval_kind")) != _MCP_TOOL_APPROVAL:
            return _DECLINE
        tool_name = f"mcp__{as_str(params.get('serverName'))}__{as_str(meta.get('tool_name'))}"
        request = self._request(
            params,
            tool_name=tool_name,
            kind="other",
            title=tool_name,
            tool_input=as_obj(meta.get("tool_params")),
        )
        return await self._decide(request)

    # -- the decision ---------------------------------------------------------------------------------------

    async def _decide(self, request: PermissionRequest) -> str:
        turn = self._turn
        if turn is None:
            return _CANCEL
        if turn.permissions is None or turn.mode != "safe":
            logger.warning("codex_approval_refused agent=%s tool=%s mode=%s", self._agent, request.tool_name, turn.mode)
            return _DECLINE
        verdict = await turn.permissions.decide(request)
        if turn.ending:
            return _CANCEL
        return _ACCEPT if verdict.allowed else _DECLINE
