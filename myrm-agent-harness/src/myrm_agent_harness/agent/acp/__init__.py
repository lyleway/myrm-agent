"""Myrm as an ACP agent: an IDE (Cursor, Zed, VS Code) connects over the Agent Client Protocol and drives it.

``toolkits/acp`` is the other direction (Myrm driving external agents) and does not know this package.
The names resolve on first access: the protocol modules need the optional ``acp`` extra and the factories bring in
the agent stack, and importing one of them must not pay for the other.

[INPUT]
- server::MyrmAcpServer, run_server (POS: ACP protocol layer) [lazy]
- bridge::AgentBridge, AgentFactory (POS: ACP session-to-agent mapping layer) [lazy]
- mcp_converter::convert_acp_mcp_servers (POS: ACP MCP schema conversion layer) [lazy]
- default_factory::DefaultAgentFactory (POS: Minimal BaseAgent factory) [lazy]
- skill_factory::SkillAgentFactory (POS: Full-featured SkillAgent factory) [lazy]

[OUTPUT]
- MyrmAcpServer: the ACP server a client connects to
- run_server: serves a factory's agents over stdio, or over the streams it is given
- AgentBridge: maps ACP sessions to agent instances
- AgentFactory: the protocol a host implements to build the agent of a session
- convert_acp_mcp_servers: the MCP servers a client hands over, as MCPConfig
- DefaultAgentFactory: a BaseAgent per session
- SkillAgentFactory: a full-featured SkillAgent per session

[POS]
Inbound ACP: the protocol server, its session bridge, and the agent factories a standalone server starts from.
"""

from importlib import import_module as _import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .bridge import AgentBridge, AgentFactory
    from .default_factory import DefaultAgentFactory
    from .mcp_converter import convert_acp_mcp_servers
    from .server import MyrmAcpServer, run_server
    from .skill_factory import SkillAgentFactory

_EXPORTS: dict[str, tuple[str, str]] = {
    "AgentBridge": (".bridge", "AgentBridge"),
    "AgentFactory": (".bridge", "AgentFactory"),
    "DefaultAgentFactory": (".default_factory", "DefaultAgentFactory"),
    "MyrmAcpServer": (".server", "MyrmAcpServer"),
    "SkillAgentFactory": (".skill_factory", "SkillAgentFactory"),
    "convert_acp_mcp_servers": (".mcp_converter", "convert_acp_mcp_servers"),
    "run_server": (".server", "run_server"),
}

__all__ = [
    "AgentBridge",
    "AgentFactory",
    "DefaultAgentFactory",
    "MyrmAcpServer",
    "SkillAgentFactory",
    "convert_acp_mcp_servers",
    "run_server",
]


def __getattr__(name: str) -> object:
    try:
        module_path, attr_name = _EXPORTS[name]
    except KeyError as exc:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from exc

    value = getattr(_import_module(module_path, __name__), attr_name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(__all__ + [name for name in globals() if not name.startswith("_")]))
