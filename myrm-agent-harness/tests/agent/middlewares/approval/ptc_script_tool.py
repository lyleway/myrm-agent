"""An MCP tool as a script reaches it, with the record the connection pool filed for it.

The approval stage reads a script's ``from skills.<skill> import <function>`` through the skill registry and the
tool registry, so a test that holds both through the production functions fails when the two stop agreeing on which
tool a call is; a test that patches the lookup seam never would.

[INPUT]
- myrm_agent_harness.agent.skills.runtime.registry::skill_registry (POS: the skills a script can import)
- myrm_agent_harness.core.security.tool_registry::register_ptc_safety_metadata (POS: the store of per-tool safety records)
- myrm_agent_harness.toolkits.mcp.config::mcp_tool_prefix (POS: how a server's tool names are prefixed)

[OUTPUT]
- connected_script_tool: serve one tool of one server under a skill name, with its record registered

[POS]
Pytest-only helper of the approval tests of PTC calls.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from myrm_agent_harness.agent.skills.runtime.registry import skill_registry
from myrm_agent_harness.backends.skills.types import MCPSkillData, SkillMetadata
from myrm_agent_harness.core.security.tool_registry import (
    MCPAnnotations,
    SafetyMetadata,
    register_ptc_safety_metadata,
)
from myrm_agent_harness.toolkits.mcp.config import mcp_tool_prefix


@contextmanager
def connected_script_tool(
    skill_name: str, server: str, tool: str, record: SafetyMetadata, claims: MCPAnnotations
) -> Iterator[str]:
    """Serve ``tool`` of ``server`` under ``skill_name`` with ``record`` registered, and yield its prefixed name."""
    tool_name = f"{mcp_tool_prefix(server)}{tool}"
    skill_registry.register(
        SkillMetadata(
            name=skill_name,
            description="demo",
            mcp=MCPSkillData(server=server, tools=[tool_name], config=[]),
        )
    )
    register_ptc_safety_metadata(tool_name, record, claims)
    try:
        yield tool_name
    finally:
        skill_registry.clear()
