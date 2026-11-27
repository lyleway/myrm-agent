"""Tests for the mcp.json input a plugin cannot use: a document that is not an object, an unsupported ``$schema``, and server entries that break the variant rules.

[INPUT]
- agent.plugins.mcp_config::decode_mcp_json, validate_mcp_top_level, parse_mcp_servers, McpConfigError (POS: decodes and validates mcp.json)

[OUTPUT]
- Fails when an absent mcp.json is not ``None`` or a leading byte order mark breaks the decode, when a document that is not an
  object or a ``$schema`` the plugin does not support is accepted or reported under another code, when a ``mcpServers`` that is
  not an object yields servers, or when an invalid server entry is accepted or takes its valid sibling down with it.

[POS]
mcp.json disables MCP only for its own top-level faults; every server is judged alone, so one bad entry must never cost the
plugin the others.
"""

from __future__ import annotations

import pytest

from myrm_agent_harness.agent.plugins.mcp_config import (
    McpConfigError,
    decode_mcp_json,
    parse_mcp_servers,
    validate_mcp_top_level,
)

MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
VALID_SERVER = {"type": "stdio", "command": "tool"}
REMOTE_URL = "https://example.com/mcp"

# One entry per rule a server has to satisfy: each is invalid for exactly the reason in its id.
INVALID_ENTRIES: dict[str, object] = {
    "not-an-object": "tool",
    "stdio-without-command": {"type": "stdio"},
    "stdio-args-not-a-list": {"type": "stdio", "command": "tool", "args": "-y"},
    "stdio-env-not-an-object": {"type": "stdio", "command": "tool", "env": ["A=1"]},
    "stdio-cwd-leaves-the-plugin": {"type": "stdio", "command": "tool", "cwd": "./../outside"},
    "stdio-unknown-field": {"type": "stdio", "command": "tool", "shell": True},
    "remote-without-url": {"type": "sse"},
    "remote-url-is-malformed": {"type": "sse", "url": "http://[::1"},
    "remote-headers-not-an-object": {"type": "sse", "url": REMOTE_URL, "headers": ["A"]},
    "remote-unknown-field": {"type": "streamable-http", "url": REMOTE_URL, "timeout": 5},
}


@pytest.mark.parametrize("entry", INVALID_ENTRIES.values(), ids=INVALID_ENTRIES.keys())
def test_an_invalid_server_is_skipped_and_its_valid_sibling_is_kept(entry: object) -> None:
    servers = parse_mcp_servers({"mcpServers": {"bad": entry, "good": VALID_SERVER}})

    assert [server.name for server in servers] == ["good"]


@pytest.mark.parametrize(
    "raw", [{}, {"mcpServers": None}, {"mcpServers": [VALID_SERVER]}], ids=["absent", "null", "array"]
)
def test_a_mcp_servers_value_that_is_not_an_object_yields_no_server(raw: dict[str, object]) -> None:
    assert parse_mcp_servers(raw) == []


def test_an_absent_document_decodes_to_none() -> None:
    assert decode_mcp_json(None) is None


def test_a_leading_byte_order_mark_is_ignored() -> None:
    assert decode_mcp_json(b'\xef\xbb\xbf{"mcpServers": {}}') == {"mcpServers": {}}


@pytest.mark.parametrize("payload", [b"[]", b'"text"', b"1", b"null"], ids=["array", "string", "number", "null"])
def test_a_document_that_is_not_an_object_is_refused(payload: bytes) -> None:
    with pytest.raises(McpConfigError) as refused:
        decode_mcp_json(payload)

    assert refused.value.code == "mcp_invalid_root"


@pytest.mark.parametrize(
    "schema", [None, 1, "https://example.com/other.schema.json"], ids=["null", "number", "other-uri"]
)
def test_an_unsupported_schema_disables_mcp(schema: object) -> None:
    validate_mcp_top_level({"$schema": MCP_SCHEMA, "mcpServers": {}}, None)

    with pytest.raises(McpConfigError) as refused:
        validate_mcp_top_level({"$schema": schema, "mcpServers": {}}, None)

    assert refused.value.code == "mcp_unsupported_schema"
