"""Tests for the integers in the JSON documents of a plugin archive: a plugin.json or mcp.json holding one with more digits than the interpreter converts is invalid input.

[INPUT]
- agent.plugins.parser::AgentPluginParser (POS: parses a plugin archive into component records)
- agent.plugins.manifest::decode_manifest_json, ManifestParseError (POS: decodes and validates plugin.json)
- agent.plugins.mcp_config::decode_mcp_json, McpConfigError (POS: decodes and validates mcp.json)

[OUTPUT]
- Fails when a plugin.json or mcp.json whose integers have as many digits as the limit is refused, or when one with an integer
  over it escapes the parser as an exception instead of a diagnostic: a plugin.json is fatal to the plugin, an mcp.json
  disables MCP and nothing else.

[POS]
``json.loads`` raises a bare ``ValueError`` for such an integer, not the ``json.JSONDecodeError`` the decoders catch; the parser
has to answer with a diagnostic, because an exception would fail the import preview with a server error.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable

import pytest

from myrm_agent_harness.agent.plugins.manifest import ManifestParseError, decode_manifest_json
from myrm_agent_harness.agent.plugins.mcp_config import McpConfigError, decode_mcp_json
from myrm_agent_harness.agent.plugins.models import PluginDiagnosticLevel
from myrm_agent_harness.agent.plugins.parser import AgentPluginParser

LIMIT = sys.get_int_max_str_digits()
pytestmark = pytest.mark.skipif(not LIMIT, reason="the interpreter converts integers of any length")

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
SKILL = b"---\nname: s\ndescription: d\n---\nBody."
AT_LIMIT = "9" * LIMIT
OVER_LIMIT = "9" * (LIMIT + 1)
OVERLONG_MESSAGE = f"Number has more than {LIMIT} digits"


def _manifest(extra: str = "") -> bytes:
    return f'{{"$schema": "{PLUGIN_SCHEMA}", "name": "demo-plugin"{extra}}}'.encode()


def _mcp(extra: str = "", server_extra: str = "") -> bytes:
    server = f'{{"type": "stdio", "command": "tool"{server_extra}}}'
    return f'{{"$schema": "{MCP_SCHEMA}", "mcpServers": {{"s": {server}}}{extra}}}'.encode()


# Where a manifest holds a number: in a field it ignores, in one it reads, and in the extension data it keeps.
MANIFEST_PLACES: dict[str, Callable[[str], bytes]] = {
    "an ignored field": lambda number: _manifest(f', "x-unknown": {number}'),
    "a field that takes a string": lambda number: _manifest(f', "version": {number}'),
    "extension data": lambda number: _manifest(f', "extensions": {{"ai.example": {{"n": {number}}}}}'),
}
# Where an mcp.json holds a number: in a field the document ignores and in a field of a server.
MCP_PLACES: dict[str, Callable[[str], bytes]] = {
    "an ignored field": lambda number: _mcp(extra=f', "x-unknown": {number}'),
    "a server field": lambda number: _mcp(server_extra=f', "timeout": {number}'),
}


@pytest.mark.parametrize("place", MANIFEST_PLACES)
def test_the_manifest_decoder_takes_an_integer_of_as_many_digits_as_the_limit(place: str) -> None:
    manifest = MANIFEST_PLACES[place](AT_LIMIT)

    assert decode_manifest_json(manifest) == json.loads(manifest)


@pytest.mark.parametrize("place", MCP_PLACES)
def test_the_mcp_decoder_takes_an_integer_of_as_many_digits_as_the_limit(place: str) -> None:
    mcp = MCP_PLACES[place](AT_LIMIT)

    assert decode_mcp_json(mcp) == json.loads(mcp)


@pytest.mark.parametrize("place", MANIFEST_PLACES)
def test_the_manifest_decoder_refuses_an_integer_over_the_limit(place: str) -> None:
    with pytest.raises(ManifestParseError, match=f"^plugin.json is not valid JSON: {OVERLONG_MESSAGE}"):
        decode_manifest_json(MANIFEST_PLACES[place](OVER_LIMIT))


@pytest.mark.parametrize("place", MCP_PLACES)
def test_the_mcp_decoder_refuses_an_integer_over_the_limit(place: str) -> None:
    with pytest.raises(McpConfigError, match=f"^mcp.json is not valid JSON: {OVERLONG_MESSAGE}") as refused:
        decode_mcp_json(MCP_PLACES[place](OVER_LIMIT))

    assert refused.value.code == "mcp_invalid_json"


@pytest.mark.parametrize("place", MANIFEST_PLACES)
def test_a_manifest_with_an_integer_over_the_limit_is_fatal_to_the_plugin_not_to_the_parser(place: str) -> None:
    files = {"plugin.json": MANIFEST_PLACES[place](OVER_LIMIT), "skills/s/SKILL.md": SKILL}

    result = AgentPluginParser().parse_files(files)

    assert result.meta is None
    assert result.skills == []
    [diagnostic] = result.diagnostics
    assert (diagnostic.component, diagnostic.code) == ("plugin", "manifest_invalid_json")
    assert diagnostic.level is PluginDiagnosticLevel.ERROR
    assert OVERLONG_MESSAGE in diagnostic.message


@pytest.mark.parametrize("place", MCP_PLACES)
def test_an_mcp_config_with_an_integer_over_the_limit_disables_mcp_and_keeps_the_skills(place: str) -> None:
    files = {"plugin.json": _manifest(), "mcp.json": MCP_PLACES[place](OVER_LIMIT), "skills/s/SKILL.md": SKILL}

    result = AgentPluginParser().parse_files(files)

    assert result.meta is not None
    assert [skill.name for skill in result.skills] == ["s"]
    assert result.servers == []
    [diagnostic] = result.diagnostics
    assert (diagnostic.component, diagnostic.code) == ("mcp", "mcp_invalid_json")
    assert OVERLONG_MESSAGE in diagnostic.message
