"""Which MCP server a tool call of an ACP agent belongs to, as the agent says it in the request for permission.

[INPUT]
- acp.schema::ToolCallUpdate (POS: ACP SDK wire type; imported only for typing, the SDK is an optional extra)

[OUTPUT]
- mcp_server_of: the name of the MCP server a tool call belongs to, among the servers a host handed the agent

[POS]
ACP has no field that names the MCP server behind a tool call, so each agent says it in the ``_meta`` of the call.
Gemini names the server (``_meta["gemini-cli"]["mcp"]["server_name"]``); Qwen names the tool as
``mcp__<server>__<tool>`` (``_meta["toolName"]``), a form that is unambiguous for the servers a host hands over because
their names never hold ``__``. A call of any other agent, or of a tool the agent has of its own, has no known origin.
"""

from __future__ import annotations

from collections.abc import Collection
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from acp.schema import ToolCallUpdate

_GEMINI = "gemini-cli"
_TOOL_PREFIX = "mcp__"


def mcp_server_of(tool_call: ToolCallUpdate, servers: Collection[str]) -> str | None:
    """The one of ``servers`` that the agent says ``tool_call`` belongs to, or ``None`` when it says none of them."""
    meta = tool_call.field_meta or {}
    gemini = meta.get(_GEMINI)
    if isinstance(gemini, dict):
        mcp = gemini.get("mcp")
        named = mcp.get("server_name") if isinstance(mcp, dict) else None
        return named if isinstance(named, str) and named in servers else None
    tool = meta.get("toolName")
    if isinstance(tool, str):
        return next((name for name in servers if tool.startswith(f"{_TOOL_PREFIX}{name}__")), None)
    return None
