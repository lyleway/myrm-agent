"""The plugin import preview answers for an archive whose JSON documents are nested too deeply for the code that stages them.

[INPUT]
- app.api.plugins.import_::router (POS: the plugin import endpoints)
- app.services.plugins._preview_context::PreviewContext (POS: the installation state the preview reads, here the default)
- myrm_agent_harness.utils.clean_json::MAX_NESTING (POS: the deepest nesting of a document the loader accepts)

[OUTPUT]
- Fails when a plugin.json or mcp.json nested deeper than ``MAX_NESTING`` levels turns the preview into a server error instead
  of a diagnostic, when a plugin.json nested to ``MAX_NESTING`` levels is refused, or when a refused mcp.json takes the skills
  of the archive down with it.

[POS]
The preview pickles the parsed archive into a staged session and echoes it in one response. ``json.loads`` reads nesting that the
pickle cannot follow (a few thousand levels) and gives up at about ten thousand; a hostile plugin.json or mcp.json must end as a
diagnostic, because a server error leaves the user with nothing to fix.
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from myrm_agent_harness.utils.clean_json import MAX_NESTING

from app.api.plugins import import_ as import_module
from app.services.plugins._preview_context import PreviewContext

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
SKILL = "---\nname: s\ndescription: d\n---\nBody."
NESTING_MESSAGE = f"Nesting is deeper than {MAX_NESTING} levels"
# A depth ``json.loads`` accepts but a pickle of the result does not, and one it cannot read at all.
TOO_DEEP_TO_STAGE = 5_000
TOO_DEEP_TO_PARSE = 20_000


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


def _object(depth: int) -> str:
    return '{"k":' * depth + "1" + "}" * depth


def _manifest(extra: str = "") -> str:
    return f'{{"$schema": "{PLUGIN_SCHEMA}", "name": "demo-plugin"{extra}}}'


def _mcp(extra: str = "") -> str:
    server = '{"type": "stdio", "command": "tool"}'
    return f'{{"$schema": "{MCP_SCHEMA}", "mcpServers": {{"s": {server}}}{extra}}}'


def _extension(depth: int) -> str:
    """A manifest field that makes the document ``depth`` levels deep: the document, ``extensions`` and the namespace take three."""
    return ', "extensions": {"ai.example": ' + _object(depth - 2) + "}"


def _unknown_field(depth: int) -> str:
    """A top-level field that makes the document ``depth`` levels deep."""
    return ', "x-unknown": ' + _object(depth - 1)


def _preview(client: TestClient, files: dict[str, str]) -> dict[str, list[dict[str, object]]]:
    buffer = io.BytesIO()
    # Stored, not deflated: a run of brackets compresses so well that the archive guard would take it for a zip bomb.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for path, text in files.items():
            archive.writestr(f"demo-plugin/{path}", text)
    response = client.post(
        "/api/v1/plugins/import/preview", files={"file": ("demo-plugin.zip", buffer.getvalue(), "application/zip")}
    )
    assert response.status_code == 200
    preview: dict[str, list[dict[str, object]]] = response.json()
    return preview


def test_a_manifest_nested_to_the_limit_is_previewed(client: TestClient) -> None:
    preview = _preview(client, {"plugin.json": _manifest(_extension(MAX_NESTING))})

    assert preview["diagnostics"] == []


@pytest.mark.parametrize(
    "field",
    [
        pytest.param(_extension(MAX_NESTING + 1), id="just over the limit"),
        pytest.param(_extension(TOO_DEEP_TO_STAGE), id="too deep to stage"),
        pytest.param(_extension(TOO_DEEP_TO_PARSE), id="too deep to parse"),
        pytest.param(_unknown_field(TOO_DEEP_TO_PARSE), id="an ignored field too deep to parse"),
    ],
)
def test_a_manifest_nested_too_deeply_is_reported_not_a_server_error(client: TestClient, field: str) -> None:
    preview = _preview(client, {"plugin.json": _manifest(field), "skills/s/SKILL.md": SKILL})

    [diagnostic] = preview["diagnostics"]
    assert diagnostic["code"] == "manifest_invalid_json"
    assert NESTING_MESSAGE in str(diagnostic["message"])
    assert preview["skills"] == []


@pytest.mark.parametrize("depth", [MAX_NESTING + 1, TOO_DEEP_TO_PARSE])
def test_an_mcp_config_nested_too_deeply_is_reported_and_the_skills_stay(client: TestClient, depth: int) -> None:
    preview = _preview(client, {"plugin.json": _manifest(), "mcp.json": _mcp(_unknown_field(depth)), "skills/s/SKILL.md": SKILL})

    [diagnostic] = preview["diagnostics"]
    assert diagnostic["code"] == "mcp_invalid_json"
    assert NESTING_MESSAGE in str(diagnostic["message"])
    assert [skill["name"] for skill in preview["skills"]] == ["s"]
    assert preview["servers"] == []
