"""``MCPConnection`` reaches a server's session from the name it is configured under or from the identity its tool names carry.

The model, the MCP App resource route and auth refresh name a server by the ``{server}`` segment of
``mcp__{server}__{tool}``, which is the identity (``server_identity``), not the name the user typed: ``Hello World`` is
``Hello_World``, ``天气`` is ``unnamed_dd4acd`` and a name past the identity bound ends in a hash. The lookup has to land
on the right session from each of them, and never on another server's.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

from myrm_agent_harness.toolkits.mcp.connection_manager import MCPConnection
from myrm_agent_harness.toolkits.mcp.server_identity import mcp_server_component

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.mcp.session_actor import MCPSessionActor

_NAMES = (
    "Hello World",
    "a.b",
    "天气",
    "我的服务",
    "my-very-long-server-name-for-the-weather-service-v2",
    "x" * 60,
)


class _Actor:
    """In-memory stand-in for ``MCPSessionActor`` that records which of its calls were reached."""

    def __init__(self, name: str) -> None:
        self.server_name = name
        self.header_updates: list[dict[str, str]] = []

    def update_auth_headers(self, headers: dict[str, str]) -> None:
        self.header_updates.append(headers)

    async def read_resource(self, uri: str) -> bytes:
        return f"{self.server_name}:{uri}".encode()


def _connection(*names: str) -> tuple[MCPConnection, dict[str, _Actor]]:
    actors = {name: _Actor(name) for name in names}
    return MCPConnection("hash", cast("dict[str, MCPSessionActor]", actors)), actors


class TestAServerIsFoundByItsName:
    def test_the_configured_name_reaches_its_session(self) -> None:
        conn, actors = _connection("天气", "weather")

        assert conn.refresh_server_auth("天气", {"X-Key": "1"}) is True
        assert actors["天气"].header_updates == [{"X-Key": "1"}]
        assert actors["weather"].header_updates == []

    def test_a_dash_or_underscore_spelling_reaches_the_same_session(self) -> None:
        conn, actors = _connection("my-server")

        assert conn.refresh_server_auth("my_server", {"X-Key": "1"}) is True
        assert actors["my-server"].header_updates == [{"X-Key": "1"}]


class TestAServerIsFoundByItsIdentity:
    @pytest.mark.parametrize("name", _NAMES)
    def test_the_identity_in_its_tool_names_reaches_its_session(self, name: str) -> None:
        conn, actors = _connection(name, "other")

        assert conn.refresh_server_auth(mcp_server_component(name), {"X-Key": "1"}) is True
        assert actors[name].header_updates == [{"X-Key": "1"}]
        assert actors["other"].header_updates == []

    @pytest.mark.asyncio
    async def test_an_app_resource_is_read_from_the_server_its_identity_names(self) -> None:
        conn, _ = _connection("天气", "我的服务")

        assert await conn.read_resource(mcp_server_component("我的服务"), "ui://card") == "我的服务:ui://card".encode()

    def test_two_servers_whose_names_fold_alike_each_keep_their_own_session(self) -> None:
        conn, actors = _connection("天气", "我的服务")

        assert conn.refresh_server_auth(mcp_server_component("天气"), {"X-Key": "1"}) is True
        assert conn.refresh_server_auth(mcp_server_component("我的服务"), {"X-Key": "2"}) is True
        assert actors["天气"].header_updates == [{"X-Key": "1"}]
        assert actors["我的服务"].header_updates == [{"X-Key": "2"}]

    def test_an_identity_no_server_has_reaches_nothing(self) -> None:
        conn, actors = _connection("天气")

        assert conn.refresh_server_auth("unnamed_000000", {"X-Key": "1"}) is False
        assert actors["天气"].header_updates == []

    @pytest.mark.asyncio
    async def test_an_unknown_server_is_still_rejected_with_the_available_names(self) -> None:
        conn, _ = _connection("天气")

        with pytest.raises(RuntimeError, match=r"MCP server not found: nope.*\['天气'\]"):
            await conn.read_resource("nope", "ui://card")
