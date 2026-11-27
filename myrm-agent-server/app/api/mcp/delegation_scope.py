"""What a delegated agent's memory credential may do on the /mcp endpoint: read, and nothing else.

[INPUT]
- mcp.server.mcpserver.exceptions::ToolError (POS: A tool failure whose message the calling model reads)
- mcp.types::Tool (POS: A tool as an MCP client lists it) [TYPE_CHECKING]

[OUTPUT]
- READ_ONLY_TOOLS: the only tools a read-only request can list or call
- read_only_request: bind whether the current MCP request carries a read-only credential
- readable_only: a tool listing as the current request may see it
- require_readable: refuse a call to a tool the current request may not use

[POS]
The write guard of the memory credentials ``app.services.external_agents.memory_grants`` hands to delegated agents. It
is an allowlist of tool names: a tool added to the endpoint later is closed to a delegated agent until it is named
here. The guard works at both ends, since an agent can call a tool it never saw listed: ``readable_only`` hides
everything else from the listing and ``require_readable`` refuses the call itself.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

from mcp.server.mcpserver.exceptions import ToolError

if TYPE_CHECKING:
    from collections.abc import Iterator

    from mcp.types import Tool

# ``wiki_query_tool`` reads only because the read-only request's wiki bundle is built without the filing of answers back
# into the vault (``wiki_tools_for_agent(read_only=True)``); in a Connect token's bundle the same tool files them.
READ_ONLY_TOOLS: frozenset[str] = frozenset({"memory_recall", "memory_list", "wiki_query_tool"})

_read_only_request: ContextVar[bool] = ContextVar("myrm_mcp_request_read_only", default=False)


@contextmanager
def read_only_request(enabled: bool) -> Iterator[None]:
    """Mark the MCP request being served as read-only (or not) for the length of the block."""
    token = _read_only_request.set(enabled)
    try:
        yield
    finally:
        _read_only_request.reset(token)


def readable_only(tools: list[Tool]) -> list[Tool]:
    """``tools`` as the current request may see them: a read-only request lists only ``READ_ONLY_TOOLS``."""
    if not _read_only_request.get():
        return tools
    return [tool for tool in tools if tool.name in READ_ONLY_TOOLS]


def require_readable(name: str) -> None:
    """Refuse a call to ``name`` when the current request is read-only and ``name`` is not a reading tool."""
    if _read_only_request.get() and name not in READ_ONLY_TOOLS:
        msg = f"This connection only reads memory: the tool {name!r} is not available to it"
        raise ToolError(msg)
