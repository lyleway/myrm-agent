"""Tests for the decode ceilings of a plugin archive: a document over its ceiling refuses the package before any loader reads it.

[INPUT]
- agent.plugins.parser::AgentPluginParser (POS: parses a plugin archive into component records)
- agent.plugins.rules::MAX_PLUGIN_JSON_BYTES, MAX_FRONTMATTER_CHARS, DocumentTooLargeError (POS: the decode ceilings and their refusal)
- agent.plugins.writer::build_plugin_bundle (POS: the exporter, whose package must stay importable)

[OUTPUT]
- Fails when a document at its ceiling is refused, when a document one unit over it is decoded or only reported as a diagnostic,
  when a loader still runs on it, when a skill body counts against the header ceiling, or when the exporter hands out a package
  the parser refuses.

[POS]
An archive is text the user did not write. The time and memory of a loader grow with the text it reads, so plugin.json, mcp.json
and the YAML header of a skill or agent file each have a ceiling, and a document over it refuses the package as a whole.
"""

from __future__ import annotations

import io
import json
import random
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from types import ModuleType

import pytest

from myrm_agent_harness.agent.plugins import frontmatter as frontmatter_module
from myrm_agent_harness.agent.plugins import manifest as manifest_module
from myrm_agent_harness.agent.plugins import mcp_config as mcp_config_module
from myrm_agent_harness.agent.plugins.manifest import decode_manifest_json
from myrm_agent_harness.agent.plugins.mcp_config import decode_mcp_json
from myrm_agent_harness.agent.plugins.models import PluginParseResult
from myrm_agent_harness.agent.plugins.parser import AgentPluginParser
from myrm_agent_harness.agent.plugins.rules import MAX_FRONTMATTER_CHARS, MAX_PLUGIN_JSON_BYTES, DocumentTooLargeError
from myrm_agent_harness.agent.plugins.writer import PluginBundleSpec, build_plugin_bundle

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
DESCRIPTION_KEY = "description: "


def _noise(size: int) -> str:
    """Hex text of exactly ``size`` characters that deflates only about two to one, so the archive passes the zip-bomb ratio."""
    return random.Random(0).randbytes((size + 1) // 2).hex()[:size]


def _manifest_with(description: str) -> str:
    return json.dumps({"$schema": PLUGIN_SCHEMA, "name": "demo-plugin", "description": description})


def _mcp_with(padding: str) -> str:
    server = {"type": "stdio", "command": "./bin/pdf", "args": [padding]}
    return json.dumps({"$schema": MCP_SCHEMA, "mcpServers": {"pdf": server}})


def _manifest_of(size: int) -> str:
    """A valid plugin.json of ``size`` bytes, padded through its description."""
    return _manifest_with(_noise(size - len(_manifest_with(""))))


def _mcp_of(size: int) -> str:
    """A valid mcp.json of ``size`` bytes, padded through the arguments of its one server."""
    return _mcp_with(_noise(size - len(_mcp_with(""))))


def _header_of(size: int) -> str:
    """A YAML header of exactly ``size`` characters: one description that starts with a letter, so it stays a string."""
    return DESCRIPTION_KEY + "x" + _noise(size - len(DESCRIPTION_KEY) - 1)


def _markdown(header: str, body: str = "Body.") -> str:
    return f"---\n{header}\n---\n{body}"


def _archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in files.items():
            archive.writestr(f"demo-plugin/{path}", text)
    return buffer.getvalue()


@dataclass(frozen=True)
class Kind:
    """One kind of document with a decode ceiling."""

    name: str
    ceiling: int
    unit: str  # what the ceiling counts
    label: str  # how a refusal names the document
    archive_of: Callable[[int], dict[str, str]]  # the archive that holds one such document of the given size
    read: Callable[[PluginParseResult], object]  # what the parse result shows once the document was read
    read_at_ceiling: object
    loader: tuple[ModuleType, str]  # the module and name of the loader that decodes the document

    def refusal(self) -> str:
        return f"{self.label} is larger than {self.ceiling:,} {self.unit} (it has {self.ceiling + 1:,})"


PLUGIN_JSON = Kind(
    name="plugin.json",
    ceiling=MAX_PLUGIN_JSON_BYTES,
    unit="bytes",
    label="plugin.json",
    archive_of=lambda size: {"plugin.json": _manifest_of(size)},
    read=lambda result: len(result.meta.description or "") if result.meta else None,
    read_at_ceiling=MAX_PLUGIN_JSON_BYTES - len(_manifest_with("")),
    loader=(manifest_module, "load_clean_json"),
)
MCP_JSON = Kind(
    name="mcp.json",
    ceiling=MAX_PLUGIN_JSON_BYTES,
    unit="bytes",
    label="mcp.json",
    archive_of=lambda size: {"plugin.json": _manifest_with(""), "mcp.json": _mcp_of(size)},
    read=lambda result: [server.name for server in result.servers],
    read_at_ceiling=["pdf"],
    loader=(mcp_config_module, "load_clean_json"),
)
SKILL_HEADER = Kind(
    name="skill header",
    ceiling=MAX_FRONTMATTER_CHARS,
    unit="characters",
    label="The front matter of skills/s/SKILL.md",
    archive_of=lambda size: {"plugin.json": _manifest_with(""), "skills/s/SKILL.md": _markdown(_header_of(size))},
    read=lambda result: len(result.skills[0].description),
    read_at_ceiling=MAX_FRONTMATTER_CHARS - len(DESCRIPTION_KEY),
    loader=(frontmatter_module, "load_clean_yaml"),
)
AGENT_HEADER = Kind(
    name="agent header",
    ceiling=MAX_FRONTMATTER_CHARS,
    unit="characters",
    label="The front matter of agent 'lead'",
    archive_of=lambda size: {"plugin.json": _manifest_with(""), "agents/lead.md": _markdown(_header_of(size))},
    read=lambda result: len(result.agents[0].description),
    read_at_ceiling=MAX_FRONTMATTER_CHARS - len(DESCRIPTION_KEY),
    loader=(frontmatter_module, "load_clean_yaml"),
)
EACH_KIND = pytest.mark.parametrize(
    "kind", [PLUGIN_JSON, MCP_JSON, SKILL_HEADER, AGENT_HEADER], ids=lambda kind: kind.name
)


@EACH_KIND
def test_a_document_at_its_ceiling_is_read(kind: Kind) -> None:
    result = AgentPluginParser().parse_zip(_archive(kind.archive_of(kind.ceiling)))

    assert kind.read(result) == kind.read_at_ceiling


@EACH_KIND
def test_a_document_one_unit_over_its_ceiling_refuses_the_package(kind: Kind) -> None:
    with pytest.raises(DocumentTooLargeError) as refusal:
        AgentPluginParser().parse_zip(_archive(kind.archive_of(kind.ceiling + 1)))

    assert str(refusal.value) == kind.refusal()


@EACH_KIND
def test_a_document_over_its_ceiling_reaches_no_loader(kind: Kind, monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse_to_decode(text: str) -> object:
        raise AssertionError("a document over its ceiling reached a loader")

    monkeypatch.setattr(*kind.loader, refuse_to_decode)

    with pytest.raises(DocumentTooLargeError):
        AgentPluginParser().parse_zip(_archive(kind.archive_of(kind.ceiling + 1)))


def test_the_ceiling_on_a_header_does_not_count_the_body_of_the_file() -> None:
    body = _noise(3 * MAX_FRONTMATTER_CHARS)
    files = {"plugin.json": _manifest_with(""), "skills/s/SKILL.md": _markdown("description: short", body)}

    result = AgentPluginParser().parse_zip(_archive(files))

    assert (result.skills[0].description, result.skills[0].content) == ("short", body)


def test_the_decoders_refuse_a_payload_over_the_ceiling_without_the_parser() -> None:
    over = b" " * (MAX_PLUGIN_JSON_BYTES + 1)

    with pytest.raises(DocumentTooLargeError, match=r"^plugin\.json is larger"):
        decode_manifest_json(over)
    with pytest.raises(DocumentTooLargeError, match=r"^mcp\.json is larger"):
        decode_mcp_json(over)


def test_the_exporter_does_not_hand_out_a_package_the_parser_refuses() -> None:
    skill = _markdown(_header_of(MAX_FRONTMATTER_CHARS + 1)).encode()

    result = build_plugin_bundle(PluginBundleSpec(name="ok", skills={"s": {"SKILL.md": skill}}))

    assert not result.success
    assert SKILL_HEADER.refusal() in (result.error or "")
