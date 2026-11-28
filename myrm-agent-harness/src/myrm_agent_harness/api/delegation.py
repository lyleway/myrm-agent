"""Public delegation surface: hand a turn to an external coding agent and decide what it may do.

[INPUT]
- toolkits.acp.acp_agent_tools::create_invoke_acp_agent_tool (POS: Delegate tasks to external ACP-compatible agents)
- toolkits.acp.client_identity::ClientIdentity (POS: How a host introduces itself to the agents it delegates to)
- toolkits.acp.mcp_servers::HttpMcpServer, McpServerConfig, StdioMcpServer (POS: The MCP servers a host hands a delegated agent)
- toolkits.acp.runtime.pool::RuntimePool (POS: Runtime pool management layer: one runtime instance per agent and workspace)
- toolkits.acp.types::the permission, event, error, session and configuration types (POS: ACP runtime type definitions layer)

[OUTPUT]
- RuntimePool, RuntimeConfig, ClientIdentity: register an agent, name the host to it, run turns on it
- RuntimeEvent, RuntimeEventType, StopReason, ContinuityStatus, AcpError, AcpErrorCode: what a turn streams back
- PermissionBroker, PermissionRequest, PermissionVerdict, PermissionMode: how the host decides what the agent may do
- CliSessionStore, CliSessionRecord: where the host keeps the agent's own conversations between turns
- StdioMcpServer, HttpMcpServer, McpServerConfig: the MCP servers a host mounts into the agent's session
- AuthMode, BackendType: the literals ``RuntimeConfig`` takes
- create_invoke_acp_agent_tool: the tool that lets a LangChain agent delegate a task to a registered agent

[POS]
Everything a host that embeds the harness needs to run turns on an agent it does not own, and nothing else. The login
and credential state of the vendors' CLIs, installing a CLI and the inbound ACP server stay internal: a host that needs
a subscription login runs the vendor's own login command.
"""

from myrm_agent_harness.toolkits.acp.acp_agent_tools import create_invoke_acp_agent_tool
from myrm_agent_harness.toolkits.acp.client_identity import ClientIdentity
from myrm_agent_harness.toolkits.acp.mcp_servers import HttpMcpServer, McpServerConfig, StdioMcpServer
from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    AcpError,
    AcpErrorCode,
    AuthMode,
    BackendType,
    CliSessionRecord,
    CliSessionStore,
    ContinuityStatus,
    PermissionBroker,
    PermissionMode,
    PermissionRequest,
    PermissionVerdict,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
    StopReason,
)

__all__ = [
    "AcpError",
    "AcpErrorCode",
    "AuthMode",
    "BackendType",
    "CliSessionRecord",
    "CliSessionStore",
    "ClientIdentity",
    "ContinuityStatus",
    "HttpMcpServer",
    "McpServerConfig",
    "PermissionBroker",
    "PermissionMode",
    "PermissionRequest",
    "PermissionVerdict",
    "RuntimeConfig",
    "RuntimeEvent",
    "RuntimeEventType",
    "RuntimePool",
    "StdioMcpServer",
    "StopReason",
    "create_invoke_acp_agent_tool",
]
