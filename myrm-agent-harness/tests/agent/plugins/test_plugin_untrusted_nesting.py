"""Tests for the nesting of the JSON documents of a plugin archive: a plugin.json or mcp.json nested too deeply is invalid input.

[INPUT]
- agent.plugins.parser::AgentPluginParser (POS: parses a plugin archive into component records)
- agent.plugins.manifest::decode_manifest_json, ManifestParseError (POS: decodes and validates plugin.json)
- agent.plugins.mcp_config::decode_mcp_json, McpConfigError (POS: decodes and validates mcp.json)
- utils.clean_json::MAX_NESTING (POS: the deepest nesting of a document the loader accepts)

[OUTPUT]
- Fails when a plugin.json or mcp.json nested to ``MAX_NESTING`` levels is refused, or when a deeper one is accepted or escapes
  the parser as an exception instead of a diagnostic: a plugin.json is fatal to the plugin, an mcp.json disables MCP and
  nothing else.

[POS]
The parser accepts nesting that the staged import session cannot be pickled with, and nesting deeper still that it cannot even
read; either would fail the import preview with a server error instead of a diagnostic.
"""

from __future__ import annotations

import json

import pytest

from myrm_agent_harness.agent.plugins.manifest import ManifestParseError, decode_manifest_json
from myrm_agent_harness.agent.plugins.mcp_config import McpConfigError, decode_mcp_json
from myrm_agent_harness.agent.plugins.models import PluginDiagnosticLevel
from myrm_agent_harness.agent.plugins.parser import AgentPluginParser
from myrm_agent_harness.utils.clean_json import MAX_NESTING

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
SKILL = b"---\nname: s\ndescription: d\n---\nBody."
NESTING_MESSAGE = f"Nesting is deeper than {MAX_NESTING} levels"
# A depth the parser accepts but a pickle of the result does not, and one the parser cannot read at all.
TOO_DEEP_TO_STAGE = 5_000
TOO_DEEP_TO_PARSE = 20_000


def _object(depth: int) -> str:
    return '{"k":' * depth + "1" + "}" * depth


def _manifest(extra: str = "") -> bytes:
    return f'{{"$schema": "{PLUGIN_SCHEMA}", "name": "demo-plugin"{extra}}}'.encode()


def _mcp(extra: str = "") -> bytes:
    server = '{"type": "stdio", "command": "tool"}'
    return f'{{"$schema": "{MCP_SCHEMA}", "mcpServers": {{"s": {server}}}{extra}}}'.encode()


def _extension(depth: int) -> str:
    """A manifest field that makes the document ``depth`` levels deep: the document, ``extensions`` and the namespace take three."""
    return ', "extensions": {"ai.example": ' + _object(depth - 2) + "}"


def _unknown_field(depth: int) -> str:
    """A top-level field that makes the document ``depth`` levels deep."""
    return ', "x-unknown": ' + _object(depth - 1)


def test_the_decoders_take_a_document_nested_to_the_limit() -> None:
    manifest = _manifest(_extension(MAX_NESTING))
    mcp = _mcp(_unknown_field(MAX_NESTING))

    assert decode_manifest_json(manifest) == json.loads(manifest)
    assert decode_mcp_json(mcp) == json.loads(mcp)


@pytest.mark.parametrize("depth", [MAX_NESTING + 1, TOO_DEEP_TO_STAGE, TOO_DEEP_TO_PARSE])
def test_the_decoders_refuse_a_deeper_document(depth: int) -> None:
    with pytest.raises(ManifestParseError, match=f"^plugin.json is not valid JSON: {NESTING_MESSAGE}"):
        decode_manifest_json(_manifest(_extension(depth)))
    with pytest.raises(McpConfigError, match=f"^mcp.json is not valid JSON: {NESTING_MESSAGE}") as refused:
        decode_mcp_json(_mcp(_unknown_field(depth)))
    assert refused.value.code == "mcp_invalid_json"


def test_a_manifest_at_the_limit_is_parsed_with_its_extension() -> None:
    result = AgentPluginParser().parse_files({"plugin.json": _manifest(_extension(MAX_NESTING))})

    assert result.diagnostics == []
    assert result.meta is not None
    assert "ai.example" in result.meta.extensions


@pytest.mark.parametrize(
    "field",
    [
        pytest.param(_extension(MAX_NESTING + 1), id="just over the limit"),
        pytest.param(_extension(TOO_DEEP_TO_STAGE), id="too deep to stage"),
        pytest.param(_extension(TOO_DEEP_TO_PARSE), id="too deep to parse"),
        pytest.param(_unknown_field(TOO_DEEP_TO_PARSE), id="an ignored field"),
    ],
)
def test_a_manifest_nested_too_deeply_is_fatal_to_the_plugin_not_to_the_parser(field: str) -> None:
    result = AgentPluginParser().parse_files({"plugin.json": _manifest(field), "skills/s/SKILL.md": SKILL})

    assert result.meta is None
    assert result.skills == []
    [diagnostic] = result.diagnostics
    assert (diagnostic.component, diagnostic.code) == ("plugin", "manifest_invalid_json")
    assert diagnostic.level is PluginDiagnosticLevel.ERROR
    assert NESTING_MESSAGE in diagnostic.message


@pytest.mark.parametrize("depth", [MAX_NESTING + 1, TOO_DEEP_TO_PARSE])
def test_an_mcp_config_nested_too_deeply_disables_mcp_and_keeps_the_skills(depth: int) -> None:
    result = AgentPluginParser().parse_files(
        {"plugin.json": _manifest(), "mcp.json": _mcp(_unknown_field(depth)), "skills/s/SKILL.md": SKILL}
    )

    assert result.meta is not None
    assert [skill.name for skill in result.skills] == ["s"]
    assert result.servers == []
    [diagnostic] = result.diagnostics
    assert (diagnostic.component, diagnostic.code) == ("mcp", "mcp_invalid_json")
    assert NESTING_MESSAGE in diagnostic.message
