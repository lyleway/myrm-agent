"""Tests that a plugin document over its decode ceiling is a refused install, not an exception.

[INPUT]
- agent.skills.market.file_install::DOCUMENT_TOO_LARGE_MESSAGE, install_skill_files, resolve_install_error (POS: the install entry, the error mapping and the fixed user sentence under test)
- agent.skills.market.service::BaseSkillMarketService (POS: the market install a user reaches the pipeline through)
- agent.plugins.rules::MAX_PLUGIN_JSON_BYTES, MAX_FRONTMATTER_CHARS, DocumentTooLargeError (POS: the decode ceilings and their refusal)

[OUTPUT]
- Fails when a document at its ceiling is refused, when a document over it escapes the install as an exception, loses its
  error code, shows the user the parser's text instead of the fixed sentence, keeps that text out of the log, leaves
  anything on disk or in the quarantine, or touches the version already installed.

[POS]
The parse of a plugin package runs inside the install transaction, so a refusal there must reach the caller as a failed
SkillInstallResult like every other gate; an exception would surface as an HTTP 500. The user sees one fixed sentence
(no file name, no number); the parser's own text goes to the log.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from myrm_agent_harness.agent.plugins.rules import MAX_FRONTMATTER_CHARS, MAX_PLUGIN_JSON_BYTES, DocumentTooLargeError
from myrm_agent_harness.agent.skills.market.file_install import (
    DOCUMENT_TOO_LARGE_MESSAGE,
    install_skill_files,
    resolve_install_error,
)
from myrm_agent_harness.agent.skills.market.installers.base import InstalledSkillFiles
from myrm_agent_harness.agent.skills.market.service import BaseSkillMarketService
from myrm_agent_harness.backends.skills.market_protocols import SkillInstallResult, SkillSearchResult

INSTALL_LOGGER = "myrm_agent_harness.agent.skills.market.file_install"
PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
DESCRIPTION_KEY = "description: "


def _manifest_with(description: str) -> str:
    return json.dumps({"$schema": PLUGIN_SCHEMA, "name": "demo-plugin", "description": description})


def _mcp_with(padding: str) -> str:
    server = {"type": "stdio", "command": "./bin/pdf", "args": [padding]}
    return json.dumps({"$schema": MCP_SCHEMA, "mcpServers": {"pdf": server}})


def _skill_md(header_size: int) -> bytes:
    """A skill file whose YAML header is exactly ``header_size`` characters: one description that stays a string."""
    header = DESCRIPTION_KEY + "x" * (header_size - len(DESCRIPTION_KEY))
    return f"---\n{header}\n---\nBody.".encode()


def _small_package() -> dict[str, bytes]:
    return {"plugin.json": _manifest_with("d").encode(), "skills/s/SKILL.md": _skill_md(len(DESCRIPTION_KEY) + 1)}


@dataclass(frozen=True)
class Kind:
    """One kind of plugin document with a decode ceiling."""

    name: str
    ceiling: int
    unit: str  # what the ceiling counts
    label: str  # how a refusal names the document
    package_of: Callable[[int], dict[str, bytes]]  # the package that holds one such document of the given size

    def refusal(self) -> str:
        return f"{self.label} is larger than {self.ceiling:,} {self.unit} (it has {self.ceiling + 1:,})"


PLUGIN_JSON = Kind(
    "plugin.json",
    MAX_PLUGIN_JSON_BYTES,
    "bytes",
    "plugin.json",
    lambda size: {**_small_package(), "plugin.json": _manifest_with("x" * (size - len(_manifest_with("")))).encode()},
)
MCP_JSON = Kind(
    "mcp.json",
    MAX_PLUGIN_JSON_BYTES,
    "bytes",
    "mcp.json",
    lambda size: {**_small_package(), "mcp.json": _mcp_with("x" * (size - len(_mcp_with("")))).encode()},
)
SKILL_HEADER = Kind(
    "skill header",
    MAX_FRONTMATTER_CHARS,
    "characters",
    "The front matter of skills/s/SKILL.md",
    lambda size: {**_small_package(), "skills/s/SKILL.md": _skill_md(size)},
)
KINDS = [PLUGIN_JSON, MCP_JSON, SKILL_HEADER]


def _files_on_disk(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in sorted(root.rglob("*")) if path.is_file()}


def test_resolve_install_error_gives_an_oversize_document_its_own_code_and_the_fixed_sentence() -> None:
    reason = "plugin.json is larger than 1,048,576 bytes (it has 1,048,577)"

    assert resolve_install_error(DocumentTooLargeError(reason)) == (DOCUMENT_TOO_LARGE_MESSAGE, "DOCUMENT_TOO_LARGE")


def test_the_fixed_sentence_names_no_file_and_no_number() -> None:
    assert not any(character.isdigit() for character in DOCUMENT_TOO_LARGE_MESSAGE)
    assert not any(
        name in DOCUMENT_TOO_LARGE_MESSAGE for name in ("plugin.json", "mcp.json", "SKILL.md", "front matter")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS, ids=lambda kind: kind.name)
async def test_a_document_at_its_ceiling_installs(tmp_path: Path, kind: Kind) -> None:
    result = await install_skill_files(tmp_path / "skills", "demo-id", "demo", kind.package_of(kind.ceiling))

    assert (result.success, result.error_code) == (True, "")


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS, ids=lambda kind: kind.name)
async def test_a_document_over_its_ceiling_is_a_refused_install(
    tmp_path: Path, quarantine_root: Path, kind: Kind
) -> None:
    install_root = tmp_path / "skills"

    result = await install_skill_files(install_root, "demo-id", "demo", kind.package_of(kind.ceiling + 1))

    assert (result.success, result.error_code, result.error) == (
        False,
        "DOCUMENT_TOO_LARGE",
        DOCUMENT_TOO_LARGE_MESSAGE,
    )
    assert not install_root.exists()
    assert list(quarantine_root.iterdir()) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", KINDS, ids=lambda kind: kind.name)
async def test_the_log_keeps_the_parser_text_the_user_does_not_see(
    tmp_path: Path, caplog: pytest.LogCaptureFixture, kind: Kind
) -> None:
    with caplog.at_level(logging.WARNING, logger=INSTALL_LOGGER):
        await install_skill_files(tmp_path / "skills", "demo-id", "demo", kind.package_of(kind.ceiling + 1))

    rejection = [record.getMessage() for record in caplog.records if "install rejected" in record.getMessage()]
    assert [kind.refusal() in line for line in rejection] == [True]


@pytest.mark.asyncio
async def test_a_refused_package_leaves_the_installed_version_as_it_was(tmp_path: Path) -> None:
    install_root = tmp_path / "skills"
    installed = await install_skill_files(install_root, "demo-id", "demo", _small_package())
    assert installed.success is True
    before = _files_on_disk(install_root)

    result = await install_skill_files(
        install_root, "demo-id", "demo", SKILL_HEADER.package_of(MAX_FRONTMATTER_CHARS + 1)
    )

    assert result.error_code == "DOCUMENT_TOO_LARGE"
    assert _files_on_disk(install_root) == before


async def _market_install(entry: str, files: dict[str, bytes], install_root: Path) -> SkillInstallResult:
    """Run one market entry on a download that holds ``files``, with its install root redirected to ``install_root``."""
    service = BaseSkillMarketService()
    downloaded = InstalledSkillFiles(name="demo", description="d", files=files)
    detail = SkillSearchResult(
        id="repo:demo",
        name="demo",
        description="d",
        source="repo",
        author="a",
        install_url="https://example.invalid",
        install_method="zip",
    )
    with patch("myrm_agent_harness.agent.skills.market.service.LOCAL_INSTALL_DIR", install_root):
        if entry == "install":
            with (
                patch.object(BaseSkillMarketService, "get_detail", return_value=detail),
                patch.object(type(service._zip_installer), "download", new=AsyncMock(return_value=downloaded)),
            ):
                return await service.install("repo:demo", "repo")
        with patch.object(type(service._git_installer), "download", new=AsyncMock(return_value=downloaded)):
            return await service.install_from_url("https://github.com/owner/repo")


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["install", "install_from_url"])
async def test_the_market_installs_a_skill_header_at_its_ceiling_into_the_redirected_root(
    tmp_path: Path, entry: str
) -> None:
    install_root = tmp_path / "skills"

    result = await _market_install(entry, SKILL_HEADER.package_of(MAX_FRONTMATTER_CHARS), install_root)

    assert (result.success, result.error_code) == (True, "")
    assert (install_root / "demo").is_dir()


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["install", "install_from_url"])
async def test_the_market_answers_an_oversize_skill_header_with_a_failed_result(tmp_path: Path, entry: str) -> None:
    install_root = tmp_path / "skills"

    result = await _market_install(entry, SKILL_HEADER.package_of(MAX_FRONTMATTER_CHARS + 1), install_root)

    assert (result.success, result.error_code, result.error) == (
        False,
        "DOCUMENT_TOO_LARGE",
        DOCUMENT_TOO_LARGE_MESSAGE,
    )
    assert not install_root.exists()
