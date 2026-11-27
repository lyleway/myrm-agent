"""Integration: a plugin archive whose JSON documents are hostile is answered and imported through the real stack.

[INPUT]
- app.api.plugins::import_router (POS: the preview and confirm endpoints, run on the real installation state)
- app.core.skills.store.service::skills_service, app.core.skills.marketplace.market_service (POS: the skill store the import writes to)
- myrm_agent_harness.utils.clean_json::MAX_NESTING (POS: the deepest nesting of a document the loader accepts)

[OUTPUT]
- Fails when a plugin.json or mcp.json that is nested too deeply, or built to make the nesting scan slow, turns the preview into
  a server error or holds it for seconds, or when a refused mcp.json stops the skills of the same archive from being installed.

[POS]
``tests/api/plugins`` runs the same archives against a route whose installation state and security scan are replaced. This
module sends them through the real route, the real preview context, the real skill scan and the real skill store, with
nothing mocked on the path, to show that the diagnostic reaches the user and that the rest of the archive still installs.
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from myrm_agent_harness.utils.clean_json import MAX_NESTING

from app.api.plugins import import_router
from app.api.plugins.import_ import PluginConfirmComponent, PluginImportConfirmRequest, PluginImportPreviewResponse
from app.core.skills.marketplace.market_service import market_service
from app.core.skills.store.service import skills_service

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
SKILL_NAME = "hostile-json-notes"
SKILL_PATH = f"skills/{SKILL_NAME}/SKILL.md"
SKILL = f"---\nname: {SKILL_NAME}\ndescription: Keeps notes tidy\n---\n\n# Notes\n\nFile every finding.\n"
# A depth the parser accepts but a pickle of the staged session does not.
TOO_DEEP_TO_STAGE = 5_000
# Escaped quotes make a 40 KiB text that the scan took tens of seconds to read, where reading it once takes milliseconds.
ESCAPED_QUOTES = 20_000
# Far above what the request costs once the text is read once, far below what rereading it from every quote cost.
PREVIEW_TIME_LIMIT = 5.0


@pytest.fixture(autouse=True)
def _skill_state_manager(tmp_path: Path) -> Iterator[None]:
    """Mirror app startup: enabling an installed skill reads the global SkillStateManager."""
    import app.core.skills.state_manager_instance as smi

    previous = smi._state_manager
    smi.init_state_manager(base_dir=str(tmp_path / "skill-state"))
    yield
    smi._state_manager = previous


@pytest.fixture
async def installed_skills() -> AsyncIterator[list[str]]:
    """Ids of skills installed by a test; they are uninstalled afterwards."""
    ids: list[str] = []
    yield ids
    for skill_id in ids:
        await market_service.uninstall(skill_id)


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = FastAPI()
    app.include_router(import_router, prefix="/api/plugins")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


def _nested(depth: int) -> str:
    return '{"k":' * depth + "1" + "}" * depth


def _escaped_quotes() -> str:
    """Openers that pass the cheap count, then a quote whose literal runs to the end and fails there."""
    return "[]" * (MAX_NESTING + 6) + '"' + '\\"' * ESCAPED_QUOTES + "\\\n"


def _archive(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    # Stored, not deflated: repeated text compresses so well that the archive guard would take it for a zip bomb.
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for path, text in files.items():
            archive.writestr(f"hostile-plugin/{path}", text)
    return buffer.getvalue()


def _manifest(extra: str = "") -> str:
    return f'{{"$schema": "{PLUGIN_SCHEMA}", "name": "hostile-plugin"{extra}}}'


def _mcp_config(extra: str = "") -> str:
    server = json.dumps({"type": "stdio", "command": "tool"})
    return f'{{"$schema": "{MCP_SCHEMA}", "mcpServers": {{"s": {server}}}{extra}}}'


async def _preview(client: AsyncClient, files: dict[str, str]) -> PluginImportPreviewResponse:
    response = await client.post(
        "/api/plugins/import/preview", files={"file": ("hostile-plugin.zip", _archive(files), "application/zip")}
    )
    assert response.status_code == 200, response.text
    return PluginImportPreviewResponse.model_validate(response.json())


@pytest.mark.asyncio
async def test_a_manifest_nested_too_deeply_is_reported_and_nothing_is_staged(client: AsyncClient) -> None:
    files = {"plugin.json": _manifest(f', "x-unknown": {_nested(TOO_DEEP_TO_STAGE)}'), SKILL_PATH: SKILL}

    preview = await _preview(client, files)

    [diagnostic] = preview.diagnostics
    assert diagnostic.code == "manifest_invalid_json"
    assert f"Nesting is deeper than {MAX_NESTING} levels" in diagnostic.message
    assert preview.skills == []


@pytest.mark.asyncio
async def test_a_refused_mcp_config_costs_the_connectors_and_not_the_skills(
    client: AsyncClient, installed_skills: list[str]
) -> None:
    files = {
        "plugin.json": _manifest(),
        "mcp.json": _mcp_config(f', "x-unknown": {_nested(MAX_NESTING + 1)}'),
        SKILL_PATH: SKILL,
    }

    preview = await _preview(client, files)

    [diagnostic] = preview.diagnostics
    assert diagnostic.code == "mcp_invalid_json"
    assert preview.servers == []
    [skill] = preview.skills
    assert skill.name == SKILL_NAME
    request = PluginImportConfirmRequest(
        session_id=preview.session_id,
        skills=[PluginConfirmComponent(component="skill", virtual_id=skill.virtual_id, name=skill.name, resolution="install")],
        servers=[],
        agents=[],
    )
    response = await client.post("/api/plugins/import/confirm", json=request.model_dump())
    assert response.status_code == 200, response.text
    result = response.json()
    assert (result["imported_skills"], result["imported_servers"], result["failures"]) == (1, 0, [])
    [installed] = [item for item in await skills_service.list_skills() if item.name == SKILL_NAME]
    installed_skills.append(installed.id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("document", "code"),
    [("plugin.json", "manifest_invalid_json"), ("mcp.json", "mcp_invalid_json")],
)
async def test_a_document_of_escaped_quotes_is_answered_within_seconds(client: AsyncClient, document: str, code: str) -> None:
    files = {"plugin.json": _manifest(), SKILL_PATH: SKILL}
    files[document] = _escaped_quotes()

    started = time.perf_counter()
    preview = await _preview(client, files)
    elapsed = time.perf_counter() - started

    assert [diagnostic.code for diagnostic in preview.diagnostics] == [code]
    assert elapsed < PREVIEW_TIME_LIMIT, f"the preview took {elapsed:.1f} s"
