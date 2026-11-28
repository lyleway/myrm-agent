"""The 64-character budget of an MCP tool name (``tool_name.py``, ``config.mcp_tool_name``).

OpenAI documents 64 characters as the longest function name (Anthropic 128), so no ``mcp__{server}__{tool}`` the model
sees may exceed 64. A name that fits stays byte for byte; a longer one keeps its readable head and ends with a
digest of the whole name, always behind its server's own prefix, so everything keyed by prefix keeps working. The two places
that put a name together (registration and bare-name lookup) go through one function and must agree on every name.
"""

from __future__ import annotations

import hashlib
import random
import re
import string
from types import SimpleNamespace

import pytest
from langchain_core.tools import BaseTool, StructuredTool

from myrm_agent_harness.toolkits.mcp.config import (
    mcp_tool_name,
    mcp_tool_prefix,
    parse_mcp_tool_name,
    sanitize_mcp_name_component,
)
from myrm_agent_harness.toolkits.mcp.server_identity import mcp_server_component
from myrm_agent_harness.toolkits.mcp.session_actor import MCPSessionActor
from myrm_agent_harness.toolkits.mcp.session_actor_tools import resolve_actor_tool
from myrm_agent_harness.toolkits.mcp.tool_name import (
    MAX_IDENTITY_LENGTH,
    MAX_TOOL_NAME_LENGTH,
    TOOL_NAME_DIGEST_LENGTH,
    clamp_tool_name,
)
from myrm_agent_harness.toolkits.mcp.tool_processing import prefix_tool_names

_SERVERS = ["github", "my-server", "天气", "高德 MCP", "a" * MAX_IDENTITY_LENGTH, "a" * 200, "MCP 服务 " * 20, "", "😀"]
_LONG_TOOL = "get_forecast_for_a_city_with_hourly_resolution_and_extended_horizon"
_UNICODE_ALPHABET = string.printable + "天气我的服务日本語サーバー서버😀é\u0301ß\u200b"


def _digest(natural: str) -> str:
    """The digest a clamped name ends with, written out independently of the module."""
    return hashlib.sha256(natural.encode("utf-8")).hexdigest()[:8]


def _tool(name: str) -> StructuredTool:
    async def run() -> str:
        return "ok"

    return StructuredTool(
        name=name, description="a tool", args_schema={"type": "object", "properties": {}}, coroutine=run
    )


def test_the_longest_identity_still_leaves_room_for_a_digest() -> None:
    prefix = mcp_tool_prefix("a" * MAX_IDENTITY_LENGTH)

    assert len(prefix) + TOOL_NAME_DIGEST_LENGTH == MAX_TOOL_NAME_LENGTH


class TestANameThatFitsIsLeftAlone:
    @pytest.mark.parametrize("server", _SERVERS)
    def test_every_length_up_to_the_limit_is_returned_byte_for_byte(self, server: str) -> None:
        prefix = mcp_tool_prefix(server)

        for length in range(1, MAX_TOOL_NAME_LENGTH - len(prefix) + 1):
            segment = "t" * length
            assert mcp_tool_name(server, segment) == f"{prefix}{segment}"

    def test_a_name_of_exactly_the_limit_is_not_clamped(self) -> None:
        segment = "t" * (MAX_TOOL_NAME_LENGTH - len(mcp_tool_prefix("weather")))

        name = mcp_tool_name("weather", segment)

        assert len(name) == MAX_TOOL_NAME_LENGTH
        assert name == f"mcp__weather__{segment}"


class TestANameOverTheLimitIsClamped:
    def test_one_character_over_ends_with_the_digest_of_the_whole_name(self) -> None:
        segment = "t" * 51
        natural = f"mcp__weather__{segment}"

        name = mcp_tool_name("weather", segment)

        assert name == f"mcp__weather__{'t' * 41}_{_digest(natural)}"
        assert len(name) == MAX_TOOL_NAME_LENGTH

    def test_a_long_tool_of_a_chinese_server_keeps_its_own_prefix(self) -> None:
        assert mcp_tool_name("天气", _LONG_TOOL) == "mcp__unnamed_dd4acd__get_forecast_for_a_city_with_hourl_00459b54"

    def test_the_longest_prefix_keeps_only_the_digest(self) -> None:
        name = mcp_tool_name("a" * MAX_IDENTITY_LENGTH, "x" * 100)

        assert name == f"mcp__{'a' * MAX_IDENTITY_LENGTH}__3ef464ee"
        assert len(name) == MAX_TOOL_NAME_LENGTH

    def test_a_cut_that_lands_on_an_underscore_never_doubles_the_delimiter(self) -> None:
        segment = "a" * 40 + "_" + "b" * 30

        name = mcp_tool_name("weather", segment)

        assert name == f"mcp__weather__{'a' * 40}_6582498d"
        assert parse_mcp_tool_name(name) == ("weather", f"{'a' * 40}_6582498d")

    @pytest.mark.parametrize("server", _SERVERS)
    def test_no_length_exceeds_the_limit_and_the_server_prefix_survives(self, server: str) -> None:
        prefix = mcp_tool_prefix(server)

        for length in range(0, 300):
            name = mcp_tool_name(server, "t" * length)

            assert len(name) <= MAX_TOOL_NAME_LENGTH, (server, length)
            assert name.startswith(prefix), (server, length)
            parsed = parse_mcp_tool_name(name)
            assert parsed is not None
            assert parsed[0] == mcp_server_component(server)
            assert parsed[1], (server, length)

    def test_tools_that_differ_only_after_the_cut_stay_distinct(self) -> None:
        first = mcp_tool_name("weather", "t" * 60 + "_alpha")
        second = mcp_tool_name("weather", "t" * 60 + "_beta")

        assert first != second
        assert first[:-9] == second[:-9]  # the readable head is the same; only the digest tells them apart

    def test_the_same_long_tool_of_two_servers_stays_distinct(self) -> None:
        assert mcp_tool_name("github", _LONG_TOOL * 2) != mcp_tool_name("gitlab", _LONG_TOOL * 2)

    def test_clamping_is_deterministic(self) -> None:
        assert mcp_tool_name("天气", _LONG_TOOL * 3) == mcp_tool_name("天气", _LONG_TOOL * 3)


def test_clamp_tool_name_takes_the_prefix_and_the_segment_as_they_are() -> None:
    assert clamp_tool_name("mcp__srv__", "tool") == "mcp__srv__tool"
    assert clamp_tool_name("mcp__srv__", "t" * 100) == f"mcp__srv__{'t' * 45}_{_digest('mcp__srv__' + 't' * 100)}"


def test_random_servers_and_tools_keep_every_promise_at_once() -> None:
    rng = random.Random(20261129)
    for _ in range(2000):
        server = "".join(rng.choice(_UNICODE_ALPHABET) for _ in range(rng.randint(0, 120)))
        tool = "".join(rng.choice(_UNICODE_ALPHABET) for _ in range(rng.randint(0, 120)))
        prefix = mcp_tool_prefix(server)
        segment = sanitize_mcp_name_component(tool)

        name = mcp_tool_name(server, segment)

        assert len(name) <= MAX_TOOL_NAME_LENGTH, (server, tool)
        assert name.startswith(prefix), (server, tool)
        assert re.fullmatch(r"mcp__[A-Za-z0-9_]+?__[A-Za-z0-9_]+", name), (server, tool)
        assert "__" not in name[len(prefix) :], (server, tool)
        if len(prefix) + len(segment) <= MAX_TOOL_NAME_LENGTH:
            assert name == f"{prefix}{segment}", (server, tool)


class TestRegistrationAndLookupAgree:
    @pytest.mark.parametrize("server", _SERVERS)
    def test_prefix_tool_names_gives_each_tool_the_name_mcp_tool_name_computes(self, server: str) -> None:
        originals = ["search", "t" * 40, "t" * 51, _LONG_TOOL, "x-" * 60]
        tools: list[BaseTool] = [_tool(name) for name in originals]

        prefix_tool_names(tools, server)

        assert [tool.name for tool in tools] == [mcp_tool_name(server, original) for original in originals]
        assert all(len(tool.name) <= MAX_TOOL_NAME_LENGTH for tool in tools)

    def test_a_bare_long_tool_name_resolves_to_the_tool_registered_under_its_clamped_name(self) -> None:
        actor = MCPSessionActor("天气", {"transport": "stdio"})
        registered = _tool(_LONG_TOOL)
        prefix_tool_names([registered], "天气")
        actor._tools = {registered.name: registered}

        assert registered.name == mcp_tool_name("天气", _LONG_TOOL)
        assert resolve_actor_tool(actor, _LONG_TOOL) is registered
        assert resolve_actor_tool(actor, registered.name) is registered

    def test_a_clamped_name_that_is_not_registered_resolves_to_nothing(self) -> None:
        actor = MCPSessionActor("天气", {"transport": "stdio"})
        actor._tools = {"mcp__unnamed_dd4acd__known": SimpleNamespace(name="mcp__unnamed_dd4acd__known")}  # type: ignore[dict-item]

        assert resolve_actor_tool(actor, mcp_tool_name("天气", _LONG_TOOL)) is None
