"""Tests that a market package whose plugin.json or mcp.json is nested too deeply installs like one whose JSON is cut off.

[INPUT]
- agent.skills.market.file_install::install_skill_files (POS: the install a downloaded package goes through, plugin parse included)
- utils.clean_json::MAX_NESTING (POS: the deepest nesting of a document the loader accepts)

[OUTPUT]
- Fails when a plugin.json or mcp.json nested to ``MAX_NESTING`` levels installs differently from a valid one, when one a level
  deeper escapes the install as an exception or installs differently from a cut-off document, or when the valid package no
  longer differs from the cut-off one (the control that keeps the comparison from being empty).

[POS]
The market parses the plugin documents of a package its supplier controls and ignores the parser's diagnostics: a plugin.json
it cannot read costs the package its skills, an mcp.json its declared servers, and the package still installs. Nesting past the
limit has to land in that same class, not raise out of the install transaction and not be read.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from myrm_agent_harness.agent.skills.market.file_install import install_skill_files
from myrm_agent_harness.utils.clean_json import MAX_NESTING

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
SKILL = b"---\nname: s\ndescription: d\n---\nBody."
SHALLOW = 5
CUT_OFF = '{"$schema": '

Outcome = tuple[bool, str, list[str], list[str] | None]


def _nested(levels: int) -> object:
    value: object = 1
    for _ in range(levels):
        value = {"k": value}
    return value


def _manifest(depth: int) -> str:
    """A plugin.json ``depth`` levels deep: the document and ``extensions`` enclose the nested value."""
    extensions = {"ai.example": _nested(depth - 2)}
    return json.dumps({"$schema": PLUGIN_SCHEMA, "name": "demo-plugin", "extensions": extensions})


def _mcp_config(depth: int) -> str:
    """An mcp.json ``depth`` levels deep and valid in every other respect: ``capabilities`` is where a server takes any JSON."""
    server = {"type": "stdio", "command": "./bin/pdf", "capabilities": _nested(depth - 3)}
    return json.dumps({"$schema": MCP_SCHEMA, "mcpServers": {"pdf": server}})


DOCUMENTS: dict[str, Callable[[int], str]] = {"plugin.json": _manifest, "mcp.json": _mcp_config}


async def _install(root: Path, document: str, text: str) -> Outcome:
    """Install a package whose ``document`` is ``text``; report what the install made of it."""
    files = {"plugin.json": _manifest(SHALLOW).encode(), "skills/s/SKILL.md": SKILL}
    files[document] = text.encode()
    install_root = root / "skills"

    result = await install_skill_files(install_root, "demo-id", "demo", files)

    installed = sorted(str(path.relative_to(install_root)) for path in install_root.rglob("*") if path.is_file())
    origin = install_root / "demo" / "origin.json"
    declared = json.loads(origin.read_text(encoding="utf-8")).get("declared_mcp_servers") if origin.is_file() else None
    return result.success, result.error_code, installed, declared


@pytest.mark.asyncio
@pytest.mark.parametrize("document", DOCUMENTS)
async def test_a_document_nested_to_the_limit_installs_like_a_valid_one(tmp_path: Path, document: str) -> None:
    make = DOCUMENTS[document]

    at_limit = await _install(tmp_path / "at-limit", document, make(MAX_NESTING))
    valid = await _install(tmp_path / "valid", document, make(SHALLOW))

    assert at_limit == valid


@pytest.mark.asyncio
@pytest.mark.parametrize("document", DOCUMENTS)
async def test_a_document_nested_past_the_limit_installs_like_one_that_is_cut_off(
    tmp_path: Path, document: str
) -> None:
    make = DOCUMENTS[document]

    past_limit = await _install(tmp_path / "past-limit", document, make(MAX_NESTING + 1))
    cut_off = await _install(tmp_path / "cut-off", document, CUT_OFF)
    valid = await _install(tmp_path / "valid", document, make(SHALLOW))

    assert past_limit == cut_off
    assert past_limit != valid
