"""[INPUT]
- app.core.types::MCPServerConfig / MCPServerConfig / MCPServerConfig (POS: Core types package)
- myrm_agent_harness.toolkits.mcp.config::mcp_tool_prefix (POS: the tool-name prefix every tool of one server carries)

[OUTPUT]
- extract_mcp_configs: Extract enabled MCP server configs from the frontend's mcpServers config.
- extract_org_mcp_configs: Extract org-managed MCP server configs pushed by the Control Plane.
- merge_org_mcp_configs: Append org-managed MCP servers to the user MCP config list; a user server that shares an org server's tool prefix is dropped.

[POS]
channel_bridge 配置解析包的子模块；由 __init__.py 聚合导出。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from myrm_agent_harness.toolkits.mcp.config import mcp_tool_prefix

if TYPE_CHECKING:
    from app.core.types import MCPServerConfig

logger = logging.getLogger(__name__)


def extract_mcp_configs(mcp_dict: dict[str, object] | None) -> list["MCPServerConfig"]:
    """Extract enabled MCP server configs from the frontend's mcpServers config."""
    from app.core.types import MCPServerConfig

    if not mcp_dict:
        return []

    raw_configs = mcp_dict.get("mcpConfigs")
    if not isinstance(raw_configs, list):
        return []

    result: list[MCPServerConfig] = []
    for cfg in raw_configs:
        if not isinstance(cfg, dict) or not cfg.get("enabled"):
            continue
        try:
            result.append(MCPServerConfig.model_validate(cfg))
        except Exception as e:
            logger.warning(
                "config_parsers: skipping invalid MCP config '%s': %s",
                cfg.get("name", "?"),
                e,
            )

    return result


def extract_org_mcp_configs(
    org_mcp_dict: dict[str, object] | None,
) -> list["MCPServerConfig"]:
    """Extract org-managed MCP server configs pushed by the Control Plane.

    Org MCPs are read-only and always available to every agent; the runtime merge
    via config_parsers.merge_org_mcp_configs appends them to the user config and
    tags them with ``extra_params.scope == "org"``. Structure mirrors the
    frontend ``orgMcpServers`` config: ``{"servers": [...]}``.
    """
    from app.core.types import MCPServerConfig

    if not org_mcp_dict:
        return []

    raw_servers = org_mcp_dict.get("servers")
    if not isinstance(raw_servers, list):
        return []

    result: list[MCPServerConfig] = []
    for raw in raw_servers:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        cfg = {
            **raw,
            "extra_params": {**(raw.get("extra_params") or {}), "scope": "org"},
        }
        try:
            result.append(MCPServerConfig.model_validate(cfg))
        except Exception as e:
            logger.warning(
                "config_parsers: skipping invalid org MCP config '%s': %s",
                raw.get("name", "?"),
                e,
            )

    return result


def merge_org_mcp_configs(
    user_mcp_configs: list["MCPServerConfig"] | None,
    org_mcp_dict: dict[str, object] | None,
) -> list["MCPServerConfig"]:
    """Append org-managed MCP servers to the user MCP config list.

    Org MCPs are read-only servers pushed by the Control Plane; they are always
    available to every agent regardless of user config. The order is user
    servers first, then org servers tagged with ``extra_params.scope == "org"``.
    A user server whose tool prefix equals an org server's is dropped:
    MCPClientManager.prepare_server_configs gives a prefix to the first
    server that claims it, and that must never be the user's.
    Shared by every execution entry point so the Web conversation path and all
    other entry points agree on the effective MCP set.
    """
    user_configs = list(user_mcp_configs or [])
    org_configs = extract_org_mcp_configs(org_mcp_dict)
    if not org_configs:
        return user_configs

    org_name_by_prefix: dict[str, str] = {}
    for org_cfg in org_configs:
        org_name_by_prefix.setdefault(mcp_tool_prefix(org_cfg.name), org_cfg.name)

    kept: list[MCPServerConfig] = []
    for user_cfg in user_configs:
        org_name = org_name_by_prefix.get(mcp_tool_prefix(user_cfg.name))
        if org_name is None:
            kept.append(user_cfg)
            continue
        logger.warning(
            "config_parsers: dropping user MCP server '%s': org MCP server '%s' uses the same tool prefix",
            user_cfg.name,
            org_name,
        )
    return kept + org_configs
