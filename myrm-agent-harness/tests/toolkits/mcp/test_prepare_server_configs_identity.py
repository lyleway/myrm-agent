"""``MCPClientManager.prepare_server_configs`` gives each tool prefix to one server only.

Every tool of a server is named ``mcp__{server}__{tool}`` and the registry, the approval lookup and the permission
rules key on it, so two servers with one prefix must not both run: the first valid config keeps the prefix and a later
one is logged as an error and skipped.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from myrm_agent_harness.toolkits.mcp.client import MCPClientManager
from myrm_agent_harness.toolkits.mcp.config import MCPAuthProvider, MCPConfig

_LOGGER = "myrm_agent_harness.toolkits.mcp.client"


def _server(
    name: str, *, url: str = "https://example.com/mcp", auth_provider: MCPAuthProvider | None = None
) -> MCPConfig:
    return MCPConfig(name=name, type="sse", url=url, auth_provider=auth_provider)


def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [record.getMessage() for record in caplog.records if record.levelno >= logging.ERROR]


@pytest.mark.asyncio
async def test_two_configs_with_one_name_keep_the_first_and_log_the_second(caplog: pytest.LogCaptureFixture) -> None:
    first = _server("dup", url="https://a.example.com/mcp")
    second = _server("dup", url="https://b.example.com/mcp")

    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        result = await MCPClientManager.prepare_server_configs([first, second])

    assert result == {"dup": first}
    assert _errors(caplog) == ["MCP server 'dup' skipped: its tool prefix mcp__dup__ is already used by server 'dup'"]


@pytest.mark.asyncio
async def test_names_that_fold_to_one_prefix_keep_the_first(caplog: pytest.LogCaptureFixture) -> None:
    first = _server("my_api")
    second = _server("my-api")

    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        result = await MCPClientManager.prepare_server_configs([first, second])

    assert result == {"my_api": first}
    assert _errors(caplog) == [
        "MCP server 'my-api' skipped: its tool prefix mcp__my_api__ is already used by server 'my_api'"
    ]


@pytest.mark.asyncio
async def test_chinese_names_each_keep_their_own_prefix(caplog: pytest.LogCaptureFixture) -> None:
    weather = _server("天气")
    service = _server("我的服务")

    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        result = await MCPClientManager.prepare_server_configs([weather, service])

    assert result == {"天气": weather, "我的服务": service}
    assert _errors(caplog) == []


@pytest.mark.asyncio
async def test_an_invalid_config_does_not_take_the_prefix_from_a_valid_one(caplog: pytest.LogCaptureFixture) -> None:
    invalid = MCPConfig(name="x-y", type="invalid_type")
    valid = _server("x_y")

    with caplog.at_level(logging.ERROR, logger=_LOGGER):
        result = await MCPClientManager.prepare_server_configs([invalid, valid])

    assert result == {"x_y": valid}
    assert _errors(caplog) == ["Failed to configure MCP server x-y: Unsupported transport type: invalid_type"]


@pytest.mark.asyncio
async def test_a_skipped_config_is_not_given_auth_headers() -> None:
    kept_provider = MagicMock()
    kept_provider.get_auth_headers = AsyncMock(return_value={})
    skipped_provider = MagicMock()
    skipped_provider.get_auth_headers = AsyncMock(return_value={"Authorization": "Bearer other"})

    await MCPClientManager.prepare_server_configs(
        [_server("a-b", auth_provider=kept_provider), _server("a_b", auth_provider=skipped_provider)]
    )

    kept_provider.get_auth_headers.assert_awaited_once()
    skipped_provider.get_auth_headers.assert_not_awaited()
