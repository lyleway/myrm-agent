"""Tests for the text a plugin archive carries: a surrogate escape in any of its documents must not reach the parse result.

[INPUT]
- agent.plugins.parser::AgentPluginParser (POS: parses a plugin archive into component records)
- agent.plugins.models::PluginParseResult (POS: the records whose every string a response may echo)

[OUTPUT]
- Fails when a lone surrogate (a JSON or YAML surrogate escape in plugin.json, mcp.json, a SKILL.md or an agent file) survives
  into a string, key or value of the parse result, or when anything but the lone surrogate is lost from the text.

[POS]
An archive is text the user did not write. The parser decodes four kinds of documents and a response echoes what they hold, so
one string no JSON body can encode fails the whole response.
"""

from __future__ import annotations

import dataclasses
import io
import json
import zipfile
from collections.abc import Callable

import pytest

from myrm_agent_harness.agent.plugins.models import PluginParseResult
from myrm_agent_harness.agent.plugins.parser import AgentPluginParser

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
REPLACED = "\ufffd"
LONE = "\ud800"  # json.dumps spells this one lone surrogate as an escape, which is what a hostile plugin.json holds
YAML_LONE = r"\ud800"  # the same escape as a YAML double-quoted scalar spells it


def _archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in files.items():
            archive.writestr(f"demo-plugin/{path}", text)
    return buffer.getvalue()


def _manifest(**fields: object) -> str:
    return json.dumps({"$schema": PLUGIN_SCHEMA, "name": "demo-plugin", **fields})


def _mcp(servers: dict[str, dict[str, object]]) -> str:
    return json.dumps({"$schema": MCP_SCHEMA, "mcpServers": servers})


def _markdown(frontmatter: str) -> str:
    return f"---\n{frontmatter}\n---\nBody."


def _strings(document: object) -> list[str]:
    """Every string, key or value, at any depth of a record tree."""
    found: list[str] = []
    pending: list[object] = [document]
    while pending:
        item = pending.pop()
        if isinstance(item, str):
            found.append(item)
        elif isinstance(item, dict):
            pending.extend(item.keys())
            pending.extend(item.values())
        elif isinstance(item, list | tuple):
            pending.extend(item)
    return found


SKILL = _markdown(f'name: s\ndescription: "a{YAML_LONE}b"\n"k{YAML_LONE}": "v{YAML_LONE}"')
AGENT = _markdown(
    f'name: "Lead{YAML_LONE}"\ndescription: "d{YAML_LONE}"\nskills:\n  - "sk{YAML_LONE}"\n"extra{YAML_LONE}": ["e{YAML_LONE}"]'
)
STDIO: dict[str, object] = {
    "type": "stdio",
    "command": f"./bin/x{LONE}",
    "args": [f"a{LONE}"],
    "env": {f"K{LONE}": f"v{LONE}"},
}
REMOTE: dict[str, object] = {
    "type": "streamable-http",
    "url": f"https://example.com/mcp{LONE}",
    "headers": {f"H{LONE}": f"v{LONE}"},
}


CHANNELS = [
    pytest.param(
        {"plugin.json": _manifest(), "skills/s/SKILL.md": SKILL},
        lambda r: (r.skills[0].description, r.skills[0].metadata.get(f"k{REPLACED}")),
        (f"a{REPLACED}b", f"v{REPLACED}"),
        id="skill frontmatter",
    ),
    pytest.param(
        {"plugin.json": _manifest(), "agents/lead.md": AGENT},
        lambda r: (
            r.agents[0].name,
            r.agents[0].description,
            r.agents[0].skill_names,
            r.agents[0].metadata.get(f"extra{REPLACED}"),
        ),
        (f"Lead{REPLACED}", f"d{REPLACED}", (f"sk{REPLACED}",), [f"e{REPLACED}"]),
        id="agent profile frontmatter",
    ),
    pytest.param(
        {
            "plugin.json": _manifest(
                version=f"1{LONE}",
                description=f"a{LONE}b",
                homepage=f"https://example.com/{LONE}",
                repository=f"https://example.com/r{LONE}",
                license=f"MIT{LONE}",
                author={"name": f"n{LONE}", "email": f"e{LONE}", "url": f"https://example.com/u{LONE}"},
                keywords=[f"k{LONE}"],
                extensions={"ai.myrm": {f"note{LONE}": f"v{LONE}"}},
            )
        },
        lambda r: (
            r.meta.version,
            r.meta.description,
            r.meta.homepage,
            r.meta.repository,
            r.meta.license,
            r.meta.author,
            r.meta.keywords,
            r.meta.extensions,
        ),
        (
            f"1{REPLACED}",
            f"a{REPLACED}b",
            f"https://example.com/{REPLACED}",
            f"https://example.com/r{REPLACED}",
            f"MIT{REPLACED}",
            {"name": f"n{REPLACED}", "email": f"e{REPLACED}", "url": f"https://example.com/u{REPLACED}"},
            (f"k{REPLACED}",),
            {"ai.myrm": {f"note{REPLACED}": f"v{REPLACED}"}},
        ),
        id="plugin.json fields",
    ),
    pytest.param(
        {"plugin.json": _manifest(capabilities=[f"x{LONE}"])},
        lambda r: r.diagnostics[0].message,
        f"plugin.json contains invalid capability 'x{REPLACED}'",
        id="plugin.json value echoed by a diagnostic",
    ),
    pytest.param(
        {"plugin.json": _manifest(extensions={f"ns{LONE}": "not-an-object"})},
        lambda r: r.diagnostics[0].message,
        f"extensions namespace 'ns{REPLACED}' must be an object",
        id="plugin.json key echoed by a diagnostic",
    ),
    pytest.param(
        {"plugin.json": _manifest(), "mcp.json": _mcp({f"srv{LONE}": {"type": "sse", "url": "ftp://example.com"}})},
        lambda r: (r.diagnostics[0].component, r.diagnostics[0].message),
        (f"mcp:srv{REPLACED}", f"MCP server 'srv{REPLACED}' is skipped"),
        id="mcp.json skipped server echoed by a diagnostic",
    ),
    pytest.param(
        {"plugin.json": _manifest(), "mcp.json": _mcp({f"srv{LONE}": STDIO})},
        lambda r: (
            r.servers[0].name,
            r.servers[0].command,
            r.servers[0].args,
            r.servers[0].env_key_names,
            r.servers[0].raw_env,
        ),
        (f"srv{REPLACED}", f"./bin/x{REPLACED}", [f"a{REPLACED}"], [f"K{REPLACED}"], {f"K{REPLACED}": f"v{REPLACED}"}),
        id="mcp.json stdio server",
    ),
    pytest.param(
        {"plugin.json": _manifest(), "mcp.json": _mcp({"remote": REMOTE})},
        lambda r: (r.servers[0].url, r.servers[0].headers),
        (f"https://example.com/mcp{REPLACED}", {f"H{REPLACED}": f"v{REPLACED}"}),
        id="mcp.json remote server",
    ),
]


@pytest.mark.parametrize(("files", "read", "expected"), CHANNELS)
def test_a_surrogate_escape_in_an_archive_document_becomes_the_replacement_character(
    files: dict[str, str], read: Callable[[PluginParseResult], object], expected: object
) -> None:
    result = AgentPluginParser().parse_zip(_archive(files))

    assert read(result) == expected


def test_no_string_of_the_parse_result_of_a_package_full_of_escapes_is_unencodable() -> None:
    files = {
        "plugin.json": _manifest(description=f"a{LONE}", author={"name": f"n{LONE}"}, keywords=[f"k{LONE}"]),
        "skills/s/SKILL.md": SKILL,
        "agents/lead.md": AGENT,
        "mcp.json": _mcp({f"srv{LONE}": STDIO, f"remote{LONE}": REMOTE}),
    }

    result = AgentPluginParser().parse_zip(_archive(files))

    assert (len(result.skills), len(result.agents), len(result.servers)) == (1, 1, 2)
    for text in _strings(dataclasses.asdict(result)):
        text.encode("utf-8")
