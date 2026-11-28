"""Desktop control tools on the /mcp endpoint: whether a request may use them and the session they run on.

[INPUT]
- myrm_agent_harness.toolkits.computer_use::create_desktop_session (POS: Desktop session factory) [lazy]
- app.config.computer_use_deploy::is_computer_use_deploy_supported (POS: Whether this deployment can drive a desktop) [lazy]
- app.services.agent.profile.profile_resolver::get_agent_profile_resolver, resolve_builtin_tool_flags (POS: Which built-in tools an agent profile enables) [lazy]
- app.services.agent.gateway::get_agent_gateway (POS: The desktop session of the run in progress) [lazy]
- app.ai_agents.desktop_control.gate::DesktopControlGate (POS: Permission gate of desktop actions) [lazy]

[OUTPUT]
- set_request_desktop_enabled / reset_request_desktop_enabled / get_request_desktop_enabled: bind whether the current MCP request exposes desktop tools
- is_desktop_control_enabled_for_agent: whether an agent profile and this deployment allow desktop control at all
- desktop_session_for_agent: the desktop session an external MCP caller drives for an agent
- clear_mcp_desktop_sessions: drop the cached sessions (testing and shutdown)

[POS]
The desktop half of the /mcp endpoint, split from ``endpoint`` so that module only holds memory and wiki scoping.
A run in progress shares its own desktop session; otherwise one session per agent is created on first use and cached.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.computer_use.desktop_session import DesktopSession

logger = logging.getLogger(__name__)

_mcp_desktop_sessions: dict[str, DesktopSession] = {}

_request_desktop_enabled: ContextVar[bool] = ContextVar(
    "myrm_mcp_request_desktop_enabled",
    default=False,
)


def set_request_desktop_enabled(enabled: bool) -> Token[bool]:
    """Bind whether desktop tools are exposed for the current MCP request."""
    return _request_desktop_enabled.set(enabled)


def reset_request_desktop_enabled(token: Token[bool]) -> None:
    """Restore previous desktop tools exposed flag."""
    _request_desktop_enabled.reset(token)


def get_request_desktop_enabled() -> bool:
    """Return whether desktop tools are exposed for the current MCP request."""
    return _request_desktop_enabled.get()


def clear_mcp_desktop_sessions() -> None:
    """Clear cached external MCP desktop sessions (used in testing and shutdown)."""
    _mcp_desktop_sessions.clear()


async def is_desktop_control_enabled_for_agent(agent_id: str) -> bool:
    try:
        from app.config.computer_use_deploy import is_computer_use_deploy_supported

        if not is_computer_use_deploy_supported():
            return False
        from app.services.agent.profile.profile_resolver import (
            get_agent_profile_resolver,
            resolve_builtin_tool_flags,
        )

        resolved = await get_agent_profile_resolver().resolve(agent_id)
        if resolved is None:
            return False
        flags = resolve_builtin_tool_flags(resolved.enabled_builtin_tools)
        return bool(flags.get("enable_computer_use"))
    except Exception as exc:
        logger.debug("Desktop control check failed for agent=%s: %s", agent_id, exc)
        return False


def desktop_session_for_agent(agent_id: str) -> DesktopSession | None:
    try:
        from app.services.agent.gateway import get_agent_gateway

        gateway = get_agent_gateway()
        active_session = gateway.get_active_desktop_session()
        if active_session is not None and hasattr(active_session, "desktop_snapshot"):
            return active_session
    except Exception:
        pass

    if agent_id not in _mcp_desktop_sessions:
        try:
            from myrm_agent_harness.toolkits.computer_use import create_desktop_session
            from myrm_agent_harness.toolkits.computer_use.types import (
                ComputerUseConfig,
                ExecutionMode,
            )

            from app.ai_agents.desktop_control.gate import DesktopControlGate
            from app.config.computer_use_deploy import is_computer_use_deploy_supported
            from app.config.deploy_mode import is_local_mode, is_sandbox

            auto_grant = is_sandbox() and is_computer_use_deploy_supported() and not is_local_mode()
            execution_mode = ExecutionMode.background_strict if is_local_mode() else ExecutionMode.background_best_effort
            gate = DesktopControlGate(workspace_root=None, auto_grant=auto_grant)
            config = ComputerUseConfig(execution_mode=execution_mode)
            _mcp_desktop_sessions[agent_id] = create_desktop_session(config=config, permission_callback=gate)
        except Exception as exc:
            logger.warning("Failed to create MCP desktop session for agent=%s: %s", agent_id, exc)
            return None
    return _mcp_desktop_sessions.get(agent_id)
