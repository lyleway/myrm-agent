"""The registry key of an MCP tool's safety record, and evicting a server's records by it.

A record is filed under the tool's prefixed name ``mcp__{server}__{tool}``, the name ``prefix_tool_names`` gives the
tool and the one every consumer reads it by. Evicting a server derives the prefix from the server's name with the same
function, so what ``register_tool_annotations`` filed for a server is exactly what ``evict_tool_annotations`` drops.
"""

from __future__ import annotations

import pytest
from langchain_core.tools import StructuredTool

from myrm_agent_harness.core.security.tool_registry import get_ptc_safety_metadata
from myrm_agent_harness.toolkits.mcp.annotations import ANNOTATIONS_METADATA_KEY
from myrm_agent_harness.toolkits.mcp.config import mcp_tool_prefix
from myrm_agent_harness.toolkits.mcp.tool_processing import (
    evict_tool_annotations,
    prefix_tool_names,
    register_tool_annotations,
)

_SERVER_NAMES = ["github", "my-server", "My Server!", "a__b", "中文服务", "a", "a_b", "ab", "a_b_c", "", " "]


def _tool(name: str, declared: dict[str, bool] | None = None) -> StructuredTool:
    async def run() -> str:
        return "ok"

    return StructuredTool(
        name=name,
        description="a tool",
        args_schema={"type": "object", "properties": {}},
        coroutine=run,
        metadata={ANNOTATIONS_METADATA_KEY: declared} if declared else None,
    )


def _file(server: str, *names: str, declared: dict[str, bool] | None = None) -> list[str]:
    """Prefix and register the tools the way the pool does, and return the names the records are filed under."""
    tools = [_tool(name, declared) for name in names]
    prefix_tool_names(tools, server)
    register_tool_annotations(tools)
    return [tool.name for tool in tools]


@pytest.mark.parametrize("server", _SERVER_NAMES)
def test_the_prefix_is_the_one_prefix_tool_names_builds_names_with(server: str) -> None:
    tools = [_tool("do_it")]

    prefix_tool_names(tools, server)

    assert tools[0].name == f"{mcp_tool_prefix(server)}do_it"


def test_a_servers_prefix_never_starts_the_tool_names_of_another_server() -> None:
    distinct = sorted({mcp_tool_prefix(name) for name in _SERVER_NAMES})

    for prefix in distinct:
        for other in distinct:
            if other != prefix:
                assert not f"{other}tool".startswith(prefix), (prefix, other)


@pytest.mark.parametrize("server", _SERVER_NAMES)
def test_eviction_drops_exactly_the_records_the_server_filed(server: str) -> None:
    mine = _file(server, "read", "write")
    others = [
        name
        for other in _SERVER_NAMES
        if mcp_tool_prefix(other) != mcp_tool_prefix(server)
        for name in _file(other, "read")
    ]

    assert evict_tool_annotations(server) == len(mine)

    assert all(get_ptc_safety_metadata(name) is None for name in mine)
    assert all(get_ptc_safety_metadata(name) is not None for name in others)


def test_evicting_a_server_that_filed_nothing_drops_nothing() -> None:
    kept = _file("present", "read")

    assert evict_tool_annotations("never-connected") == 0
    assert evict_tool_annotations("present") == len(kept)
    assert evict_tool_annotations("present") == 0


def test_filing_a_servers_tools_again_replaces_their_records() -> None:
    (name,) = _file("srv", "act", declared={"destructiveHint": True})
    record, claims = get_ptc_safety_metadata(name) or (None, None)
    assert record is not None
    assert record.is_destructive is True
    assert claims == {"destructiveHint": True}

    _file("srv", "act")

    record, claims = get_ptc_safety_metadata(name) or (None, None)
    assert record is not None
    assert record.is_destructive is False
    assert claims == {}
