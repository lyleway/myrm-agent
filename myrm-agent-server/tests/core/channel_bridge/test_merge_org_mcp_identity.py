"""``merge_org_mcp_configs``: a user MCP server never keeps the tool prefix of an org MCP server.

Org servers come from the Control Plane and are always available to the agent. The runtime gives a tool prefix to the
first server that claims it (``MCPClientManager.prepare_server_configs``) and the merged list holds user servers before
org servers, so a user server with an org server's prefix would shadow the org server unless the merge drops it.
"""

from __future__ import annotations

import logging

import pytest

from app.core.channel_bridge.config_parsers import extract_mcp_configs, merge_org_mcp_configs
from app.core.types import MCPServerConfig

_LOGGER = "app.core.channel_bridge.config_parsers.mcp"


def _user(*names: str) -> list[MCPServerConfig]:
    servers = [
        {"name": name, "type": "sse", "url": f"https://user.example.com/{index}", "enabled": True}
        for index, name in enumerate(names)
    ]
    return extract_mcp_configs({"mcpConfigs": servers})


def _org(*names: str) -> dict[str, object]:
    return {
        "servers": [{"name": name, "type": "sse", "url": f"https://org.example.com/{index}"} for index, name in enumerate(names)]
    }


def _names(configs: list[MCPServerConfig]) -> list[str]:
    return [config.name for config in configs]


def test_a_user_server_with_an_org_servers_name_is_dropped_and_the_org_server_stays(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.WARNING, logger=_LOGGER):
        merged = merge_org_mcp_configs(_user("crm", "db"), _org("crm"))

    assert _names(merged) == ["db", "crm"]
    assert merged[-1].extra_params == {"scope": "org"}
    assert [r.getMessage() for r in caplog.records] == [
        "config_parsers: dropping user MCP server 'crm': org MCP server 'crm' uses the same tool prefix"
    ]


@pytest.mark.parametrize(("user_name", "org_name"), [("my-api", "my_api"), ("my api", "my-api"), ("A--B", "A_B")])
def test_a_user_server_that_folds_to_an_org_servers_prefix_is_dropped(user_name: str, org_name: str) -> None:
    assert _names(merge_org_mcp_configs(_user(user_name), _org(org_name))) == [org_name]


def test_servers_with_different_prefixes_are_all_kept_in_user_then_org_order() -> None:
    """Chinese names fold to the same text; their prefixes still differ, so none of them is dropped."""
    merged = merge_org_mcp_configs(_user("天气", "github"), _org("我的服务", "GitHub"))

    assert _names(merged) == ["天气", "github", "我的服务", "GitHub"]


def test_org_servers_are_not_judged_against_each_other() -> None:
    """Two org servers with one prefix are both returned; ``prepare_server_configs`` picks the first."""
    assert _names(merge_org_mcp_configs([], _org("a-b", "a_b"))) == ["a-b", "a_b"]


def test_the_callers_user_list_is_not_modified_when_a_server_is_dropped() -> None:
    user = _user("crm", "db")

    merge_org_mcp_configs(user, _org("crm"))

    assert _names(user) == ["crm", "db"]
