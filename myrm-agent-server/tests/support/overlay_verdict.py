"""The engine's verdict for one tool call under a raw securityConfig, as a chat turn builds it.

[INPUT]
- myrm_agent_harness.agent.security.channel_presets::build_channel_security_config (POS: assembles the effective
  SecurityConfig of a channel from the saved config, the agent's config and the request's overlays)
- myrm_agent_harness.agent.security.engine::evaluate_tool_call (POS: the permission decision of one tool call)
- myrm_agent_harness.core.security.tool_registry::resolve_permission_type (POS: the permission a tool call is judged as)

[OUTPUT]
- verdict_under: the action the engine gives a call of a tool

[POS]
Tests of the request overlays assert what a call receives, not which keys an overlay wrote: a restriction that is
a layer next to the saved permissions shows only in the verdict.
"""

from __future__ import annotations

from myrm_agent_harness.agent.security.channel_presets import build_channel_security_config
from myrm_agent_harness.agent.security.engine import evaluate_tool_call
from myrm_agent_harness.agent.security.types import PermissionAction
from myrm_agent_harness.core.security.tool_registry import resolve_permission_type


def verdict_under(
    raw: dict[str, object] | None,
    tool: str,
    tool_input: dict[str, object] | None = None,
    *,
    agent_security_raw: dict[str, object] | None = None,
    channel: str = "web_chat",
    local_mode: bool = False,
) -> PermissionAction:
    """The action a call of *tool* gets under the securityConfig *raw* (the saved config with the overlays applied)."""
    arguments = tool_input or {}
    config = build_channel_security_config(
        channel,
        raw,
        agent_security_raw=agent_security_raw,
        local_mode=local_mode,
    )
    action, _ = evaluate_tool_call(
        resolve_permission_type(tool, arguments),
        arguments,
        config,
        tool_name=tool,
    )
    return action
