"""The status update that tells a host an MCP server it offered a turn did not reach the agent.

[INPUT]
- toolkits.acp.types::RuntimeEvent, RuntimeEventType, create_event (POS: ACP runtime event type and its constructor)

[OUTPUT]
- MCP_NOT_ATTACHED_STATUS: the ``status`` of that update
- mcp_not_attached_event: the update, naming the servers that were left out

[POS]
A backend either takes an MCP server or it does not (an ACP agent without ``mcpCapabilities.http`` cannot connect to an
HTTP one, a CLI that is not Claude Code reads no MCP config), and a host that offered the server is told which ones
the agent runs without, instead of finding out from an answer that lacks what the server would have given.
"""

from __future__ import annotations

from collections.abc import Sequence

from myrm_agent_harness.toolkits.acp.types import RuntimeEvent, RuntimeEventType, create_event

MCP_NOT_ATTACHED_STATUS = "mcp_not_attached"


def mcp_not_attached_event(session_id: str, servers: Sequence[str]) -> RuntimeEvent:
    """The status update naming the MCP servers (by their names) the agent was not given."""
    return create_event(
        RuntimeEventType.STATUS_UPDATE, session_id, status=MCP_NOT_ATTACHED_STATUS, servers=list(servers)
    )
