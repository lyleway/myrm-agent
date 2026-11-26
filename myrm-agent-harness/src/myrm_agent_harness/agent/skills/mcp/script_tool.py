"""Which MCP tool a script's skill import reaches.

[INPUT]
- runtime.registry::skill_registry (POS: process-wide registry of the skills an agent can import)
- tool_name_utils::resolve_mcp_tool_name (POS: single source of truth for MCP tool name matching)

[OUTPUT]
- resolve_script_tool: the prefixed MCP tool name that ``from <skill> import <function>`` reaches, or None.

[POS]
The one place a script's (skill, function) pair becomes the tool's prefixed name. The proxy resolves a call with it
for its cache and schema lookups, and the approval stage resolves the same pair with it before it reads the tool's
recorded safety metadata, so approval and execution agree on which tool a call is.
"""

from __future__ import annotations

from myrm_agent_harness.agent.skills.runtime.registry import skill_registry

from .tool_name_utils import resolve_mcp_tool_name


def resolve_script_tool(skill_name: str, function_name: str) -> str | None:
    """Return the prefixed tool name a script's call reaches; None when the skill or the function is unknown."""
    skill_meta = skill_registry.get_mcp_skill_for_ipc(skill_name)
    if skill_meta is None or skill_meta.mcp is None:
        return None
    return resolve_mcp_tool_name(function_name, skill_meta.mcp.tools)
