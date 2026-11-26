"""Tests that a refusal naming a skill no UTF-8 text can hold reaches the user as a JSON refusal, not an HTTP 500.

[INPUT]
- app.api.skills.discovery::router (POS: the install routes whose response is built from the refusal)
- agent.skills.market.file_install::install_skill_files (POS: the real gate that refuses the package and echoes its name)

[OUTPUT]
- Fails when /install or /install-from-url cannot serialize the refusal of a skill whose name holds a lone surrogate, or
  answers with anything but a failed install that keeps its error code and error text and shows the name with each lone
  surrogate replaced by U+FFFD.

[POS]
A directory name that was not valid UTF-8 reads back as text with a lone surrogate, which a JSON body cannot carry. The
refusal comes from the real install gate; only the registry fetch and the replace-snapshot entry points are stubbed, so
the route, its response builder and the response model run as in production.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from myrm_agent_harness.agent.skills.market.file_install import install_skill_files

from app.api.skills.discovery import router as discovery_router
from app.core.skills.marketplace.market_service import market_service

REPLACE_INSTALL = "app.core.skills.discovery.replace_install"
SKILL_MD = b"---\nname: demo\ndescription: A demo skill.\n---\nDo things.\n"

# The install routes: path, request body, and the replace-snapshot entry point the route's flow calls.
INSTALL_ROUTES = {
    "install": (
        "/install",
        {"skill_id": "demo", "source": "clawhub", "mount_to_agent": False},
        "market_install_with_replace_snapshot",
    ),
    "install-from-url": (
        "/install-from-url",
        {"url": "https://github.com/o/r", "mount_to_agent": False},
        "market_install_from_url_with_replace_snapshot",
    ),
}

# A refused name, how the response shows it, and the gate that refuses it.
REFUSED_NAMES = {
    "lone surrogate": ("demo\ud800", "demo\ufffd", "INVALID_PACKAGE_PATH"),
    "separator and lone surrogate": ("bad/\ud800", "bad/\ufffd", "INVALID_SKILL_NAME"),
}


@pytest.fixture
def discovery_client() -> TestClient:
    app = FastAPI(title="Discovery Install Refusals")
    app.include_router(discovery_router, prefix="/api/v1/skills")
    return TestClient(app)


@pytest.mark.parametrize("route, payload, entry_point", INSTALL_ROUTES.values(), ids=INSTALL_ROUTES.keys())
@pytest.mark.parametrize("name, shown_name, error_code", REFUSED_NAMES.values(), ids=REFUSED_NAMES.keys())
async def test_a_refusal_naming_an_unencodable_skill_is_a_json_refusal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    discovery_client: TestClient,
    route: str,
    payload: dict[str, str | bool],
    entry_point: str,
    name: str,
    shown_name: str,
    error_code: str,
) -> None:
    refusal = await install_skill_files(tmp_path / "skills", "demo-id", name, {"SKILL.md": SKILL_MD})
    monkeypatch.setattr(f"{REPLACE_INSTALL}.{entry_point}", AsyncMock(return_value=(refusal, None, False)))
    monkeypatch.setattr(market_service, "ensure_clawhub_registry", AsyncMock())

    response = discovery_client.post(f"/api/v1/skills/discovery{route}", json=payload)

    assert response.status_code == 200
    body = response.json()
    assert (body["success"], body["error_code"], body["error"], body["skill_name"]) == (
        False,
        error_code,
        refusal.error,
        shown_name,
    )
