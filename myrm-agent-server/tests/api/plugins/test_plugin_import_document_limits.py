"""The plugin import preview refuses an archive that holds a document over its decode ceiling, naming the document.

[INPUT]
- app.api.plugins.import_::router (POS: the plugin import endpoints)
- app.services.plugins._preview_context::PreviewContext (POS: the installation state the preview reads, here the default)
- myrm_agent_harness.agent.plugins.rules::MAX_PLUGIN_JSON_BYTES, MAX_FRONTMATTER_CHARS (POS: the decode ceilings)

[OUTPUT]
- Fails when an archive whose plugin.json, mcp.json, skill header or agent header is over its ceiling is previewed instead of
  answered with a 400 that names the document, when a refused archive leaves a staged session behind, or when a document at
  its ceiling stops being previewed.

[POS]
An archive is text the user did not write, so the parser refuses a document that a loader would take too long or too much
memory to read. The refusal is the answer of the whole preview: no component of the package is staged.
"""

from __future__ import annotations

import io
import json
import random
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from myrm_agent_harness.agent.plugins.rules import MAX_FRONTMATTER_CHARS, MAX_PLUGIN_JSON_BYTES

from app.api.plugins import import_ as import_module
from app.services.plugins._preview_context import PreviewContext

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
DESCRIPTION_KEY = "description: "


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    app = FastAPI(title="Plugin import preview test app")
    app.include_router(import_module.router, prefix="/api/v1/plugins")
    monkeypatch.setattr(import_module, "get_evolution_skill_store_db_path", lambda: tmp_path / "skills.db")
    monkeypatch.setattr("app.services.plugins._gates.scan_skill_security", lambda skill: [])
    monkeypatch.setattr("app.services.plugins.import_service.load_preview_context", AsyncMock(return_value=PreviewContext()))
    monkeypatch.setattr("app.services.plugins.import_service.list_installed_plugins", AsyncMock(return_value=[]))
    # An uncaught exception becomes a 500 response, which is not the refusal these tests look for.
    return TestClient(app, raise_server_exceptions=False)


def _noise(size: int) -> str:
    """Hex text of exactly ``size`` characters that deflates only about two to one, so the archive passes the zip-bomb ratio."""
    return random.Random(0).randbytes((size + 1) // 2).hex()[:size]


def _manifest_with(description: str) -> str:
    return json.dumps({"$schema": PLUGIN_SCHEMA, "name": "demo-plugin", "description": description})


def _manifest_of(size: int) -> str:
    return _manifest_with(_noise(size - len(_manifest_with(""))))


def _mcp_of(size: int) -> str:
    def document(padding: str) -> str:
        server = {"type": "stdio", "command": "./bin/pdf", "args": [padding]}
        return json.dumps({"$schema": MCP_SCHEMA, "mcpServers": {"pdf": server}})

    return document(_noise(size - len(document(""))))


def _markdown_with_header_of(size: int) -> str:
    header = DESCRIPTION_KEY + "x" + _noise(size - len(DESCRIPTION_KEY) - 1)
    return f"---\n{header}\n---\nBody."


def _preview(client: TestClient, files: dict[str, str]) -> tuple[int, dict[str, object]]:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, text in files.items():
            archive.writestr(f"demo-plugin/{path}", text)
    response = client.post(
        "/api/v1/plugins/import/preview", files={"file": ("demo-plugin.zip", buffer.getvalue(), "application/zip")}
    )
    return response.status_code, response.json()


OVER_THE_CEILING = [
    pytest.param(
        {"plugin.json": _manifest_of(MAX_PLUGIN_JSON_BYTES + 1)},
        f"plugin.json is larger than {MAX_PLUGIN_JSON_BYTES:,} bytes (it has {MAX_PLUGIN_JSON_BYTES + 1:,})",
        id="plugin.json",
    ),
    pytest.param(
        {"plugin.json": _manifest_with(""), "mcp.json": _mcp_of(MAX_PLUGIN_JSON_BYTES + 1)},
        f"mcp.json is larger than {MAX_PLUGIN_JSON_BYTES:,} bytes (it has {MAX_PLUGIN_JSON_BYTES + 1:,})",
        id="mcp.json",
    ),
    pytest.param(
        {"plugin.json": _manifest_with(""), "skills/s/SKILL.md": _markdown_with_header_of(MAX_FRONTMATTER_CHARS + 1)},
        f"The front matter of skills/s/SKILL.md is larger than {MAX_FRONTMATTER_CHARS:,} characters "
        f"(it has {MAX_FRONTMATTER_CHARS + 1:,})",
        id="skill header",
    ),
    pytest.param(
        {"plugin.json": _manifest_with(""), "agents/lead.md": _markdown_with_header_of(MAX_FRONTMATTER_CHARS + 1)},
        f"The front matter of agent 'lead' is larger than {MAX_FRONTMATTER_CHARS:,} characters "
        f"(it has {MAX_FRONTMATTER_CHARS + 1:,})",
        id="agent header",
    ),
]


@pytest.mark.parametrize(("files", "refusal"), OVER_THE_CEILING)
def test_a_document_over_its_ceiling_is_answered_with_a_400_that_names_it(
    client: TestClient, tmp_path: Path, files: dict[str, str], refusal: str
) -> None:
    status, body = _preview(client, files)

    assert (status, body) == (400, {"detail": refusal})
    assert list(tmp_path.iterdir()) == []


def test_a_document_at_its_ceiling_is_previewed_and_staged(client: TestClient, tmp_path: Path) -> None:
    status, body = _preview(client, {"plugin.json": _manifest_of(MAX_PLUGIN_JSON_BYTES)})

    assert status == 200
    assert body["plugin"] is not None
    assert list(tmp_path.iterdir()) != []
