"""The plugin import preview answers for an archive whose documents hold text the response must be able to encode.

[INPUT]
- app.api.plugins.import_::router (POS: the plugin import endpoints)
- app.services.plugins._preview_context::PreviewContext (POS: the installation state the preview reads, here the default)

[OUTPUT]
- Fails when a lone surrogate spelled as an escape in plugin.json, mcp.json, a SKILL.md or an agent file turns the preview into a
  server error, or when anything but the lone surrogate is lost from the text the preview echoes.

[POS]
An archive is text the user did not write. The parser decodes four kinds of documents and the preview echoes what they hold in one
JSON response, so each decoder must leave text that response can encode: one value that cannot be encoded fails the whole preview.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.plugins import import_ as import_module
from app.services.plugins._preview_context import PreviewContext

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
REPLACED = "\ufffd"
LONE = "\ud800"  # json.dumps spells this one lone surrogate as an escape, which is what a hostile plugin.json holds
YAML_LONE = r"\ud800"  # the same escape as a YAML double-quoted scalar spells it


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI(title="Plugin import preview test app")
    app.include_router(import_module.router, prefix="/api/v1/plugins")
    monkeypatch.setattr(import_module, "get_evolution_skill_store_db_path", lambda: tmp_path / "skills.db")
    monkeypatch.setattr("app.services.plugins._gates.scan_skill_security", lambda skill: [])
    monkeypatch.setattr("app.services.plugins.import_service.load_preview_context", AsyncMock(return_value=PreviewContext()))
    monkeypatch.setattr("app.services.plugins.import_service.list_installed_plugins", AsyncMock(return_value=[]))
    # An uncaught exception becomes a 500 response, which is the failure these tests look for.
    return TestClient(app, raise_server_exceptions=False)


def _manifest(**fields: object) -> str:
    return json.dumps({"$schema": PLUGIN_SCHEMA, "name": "demo-plugin", **fields})


def _mcp(servers: dict[str, dict[str, object]]) -> str:
    return json.dumps({"$schema": MCP_SCHEMA, "mcpServers": servers})


def _markdown(frontmatter: str) -> str:
    return f"---\n{frontmatter}\n---\nBody."


def _preview(client: TestClient, files: dict[str, str]) -> object:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in files.items():
            archive.writestr(f"demo-plugin/{path}", text)
    response = client.post(
        "/api/v1/plugins/import/preview", files={"file": ("demo-plugin.zip", buffer.getvalue(), "application/zip")}
    )
    assert response.status_code == 200
    return response.json()


def _at(document: object, path: tuple[str | int, ...]) -> object:
    for step in path:
        if isinstance(document, dict) and isinstance(step, str):
            document = document[step]
        elif isinstance(document, list) and isinstance(step, int):
            document = document[step]
        else:
            raise AssertionError(f"{step!r} does not index a {type(document).__name__}")
    return document


CHANNELS = [
    pytest.param(
        {"plugin.json": _manifest(), "skills/s/SKILL.md": _markdown(f'name: s\ndescription: "a{YAML_LONE}b"')},
        ("skills", 0, "description"),
        f"a{REPLACED}b",
        id="skill description",
    ),
    pytest.param(
        {"plugin.json": _manifest(), "agents/lead.md": _markdown(f'name: "Lead{YAML_LONE}"')},
        ("agents", 0, "name"),
        f"Lead{REPLACED}",
        id="agent name",
    ),
    pytest.param(
        {"plugin.json": _manifest(), "agents/lead.md": _markdown(f'name: lead\ndescription: "d{YAML_LONE}"')},
        ("agents", 0, "description"),
        f"d{REPLACED}",
        id="agent description",
    ),
    pytest.param(
        {"plugin.json": _manifest(), "agents/lead.md": _markdown(f'name: lead\nskills:\n  - "sk{YAML_LONE}"')},
        ("agents", 0, "skill_names"),
        [f"sk{REPLACED}"],
        id="agent skill reference",
    ),
    pytest.param(
        {"plugin.json": _manifest(description=f"a{LONE}b")},
        ("plugin", "description"),
        f"a{REPLACED}b",
        id="plugin description",
    ),
    pytest.param(
        {"plugin.json": _manifest(homepage=f"https://example.com/{LONE}", author={"name": f"n{LONE}"}, keywords=[f"k{LONE}"])},
        ("plugin", "author"),
        {"name": f"n{REPLACED}"},
        id="plugin author",
    ),
    pytest.param(
        {"plugin.json": _manifest(keywords=[f"k{LONE}"])},
        ("plugin", "keywords"),
        [f"k{REPLACED}"],
        id="plugin keyword",
    ),
    pytest.param(
        {"plugin.json": _manifest(capabilities=[f"x{LONE}"])},
        ("diagnostics", 0, "message"),
        f"plugin.json contains invalid capability 'x{REPLACED}'",
        id="plugin.json value echoed by a diagnostic",
    ),
    pytest.param(
        {"plugin.json": _manifest(extensions={f"ns{LONE}": "not-an-object"})},
        ("diagnostics", 0, "message"),
        f"extensions namespace 'ns{REPLACED}' must be an object",
        id="plugin.json key echoed by a diagnostic",
    ),
    pytest.param(
        {"plugin.json": _manifest(), "mcp.json": _mcp({f"srv{LONE}": {"type": "sse", "url": "ftp://example.com"}})},
        ("diagnostics", 0, "message"),
        f"MCP server 'srv{REPLACED}' is skipped",
        id="skipped mcp server echoed by a diagnostic",
    ),
    pytest.param(
        {"plugin.json": _manifest(), "mcp.json": _mcp({f"srv{LONE}": {"type": "stdio", "command": "./bin/pdf"}})},
        ("servers", 0, "name"),
        f"srv{REPLACED}",
        id="mcp server name",
    ),
    pytest.param(
        {"plugin.json": _manifest(), "mcp.json": _mcp({"pdf": {"type": "stdio", "command": f"./bin/pdf{LONE}"}})},
        ("servers", 0, "command"),
        f"./bin/pdf{REPLACED}",
        id="mcp command",
    ),
    pytest.param(
        {"plugin.json": _manifest(), "mcp.json": _mcp({"hosted": {"type": "sse", "url": f"https://example.com/sse{LONE}"}})},
        ("servers", 0, "url"),
        f"https://example.com/sse{REPLACED}",
        id="mcp url",
    ),
]


@pytest.mark.parametrize(("files", "path", "expected"), CHANNELS)
def test_a_surrogate_escape_in_an_archive_document_is_previewed_as_the_replacement_character(
    client: TestClient, files: dict[str, str], path: tuple[str | int, ...], expected: object
) -> None:
    assert _at(_preview(client, files), path) == expected
