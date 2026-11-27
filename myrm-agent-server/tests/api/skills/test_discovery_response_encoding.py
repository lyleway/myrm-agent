"""Tests that text a discovery response carries reaches the client as JSON even when it holds a lone surrogate.

[INPUT]
- app.api.skills.discovery::router (POS: the search route whose response is built from registry text)
- app.api.skills.discovery.schemas, app.api.skills.schemas::ScanFindingResponse (POS: the response models under test)

[OUTPUT]
- Fails when /search answers anything but 200 for a registry entry whose text holds a lone surrogate, or shows that text with
  the surrogate left in or anything else changed, or when a text field of a response model that carries registry, package or
  scanner text keeps a lone surrogate, whether the field is plain, optional or a list.

[POS]
Registry, package and scanner text is outside text: a lone surrogate (a JSON escape for U+D800 in a registry answer) has no
UTF-8 form, so a response model that carries it makes the JSON body unencodable and the route answers HTTP 500. The model
is the last place that can repair it, so a text field added without the repair fails here instead of in production.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from myrm_agent_harness.agent.skills.market.service import EnrichedSearchResult
from myrm_agent_harness.backends.skills.market_protocols import SkillSearchResult
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.api.skills.discovery import router as discovery_router
from app.api.skills.discovery.schemas import (
    PrerequisiteDiagnosticResponse,
    SkillInstallResponse,
    SkillPreviewResponse,
    SkillReceiptResponse,
    SkillSearchResultResponse,
    SkillUpdateInfoResponse,
    SkillUrlInfo,
)
from app.api.skills.schemas import ScanFindingResponse
from app.core.skills.marketplace.market_service import market_service

LONE_SURROGATE = "\ud800"

# The response models that carry text a registry, a package or the scanner supplies.
EXTERNAL_TEXT_MODELS = (
    SkillSearchResultResponse,
    SkillReceiptResponse,
    SkillInstallResponse,
    SkillUpdateInfoResponse,
    PrerequisiteDiagnosticResponse,
    SkillPreviewResponse,
    SkillUrlInfo,
    ScanFindingResponse,
)


def _kept_by_text_fields(model: type[BaseModel]) -> dict[str, object]:
    """What each text field of the model keeps of a lone surrogate; a field of any other type rejects the probe."""
    kept: dict[str, object] = {}
    for name, field in model.model_fields.items():
        adapter = TypeAdapter(field.rebuild_annotation())
        for probe in (LONE_SURROGATE, [LONE_SURROGATE]):
            try:
                kept[name] = adapter.validate_python(probe)
            except ValidationError:
                continue
    return kept


def _texts(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, list):
        return [text for item in value for text in _texts(item)]
    return []


def _has_utf8_form(text: str) -> bool:
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


@pytest.fixture
def discovery_client() -> TestClient:
    app = FastAPI(title="Discovery Response Encoding")
    app.include_router(discovery_router, prefix="/api/v1/skills")
    return TestClient(app, raise_server_exceptions=False)


def test_a_registry_entry_whose_text_holds_a_lone_surrogate_is_searchable(
    monkeypatch: pytest.MonkeyPatch, discovery_client: TestClient
) -> None:
    entry = EnrichedSearchResult(
        result=SkillSearchResult(
            id=f"demo{LONE_SURROGATE}",
            name=f"demo{LONE_SURROGATE}",
            description=f"Does {LONE_SURROGATE} things.",
            source="clawhub",
            author="someone",
            install_url="https://example.org/demo.zip",
            install_method="zip",
            tags=[f"tag{LONE_SURROGATE}"],
        )
    )
    monkeypatch.setattr(market_service, "search", AsyncMock(return_value=[entry]))
    monkeypatch.setattr(market_service, "ensure_clawhub_registry", AsyncMock())
    monkeypatch.setattr(market_service, "get_installed_local_ids_by_name", AsyncMock(return_value={}))

    response = discovery_client.get("/api/v1/skills/discovery/search", params={"q": "demo"})

    assert response.status_code == 200
    item = response.json()["results"][0]
    assert (item["id"], item["name"], item["description"], item["tags"]) == (
        "demo\ufffd",
        "demo\ufffd",
        "Does \ufffd things.",
        ["tag\ufffd"],
    )


@pytest.mark.parametrize("model", EXTERNAL_TEXT_MODELS, ids=lambda model: model.__name__)
def test_every_text_field_of_a_response_model_repairs_a_lone_surrogate(model: type[BaseModel]) -> None:
    kept = _kept_by_text_fields(model)

    assert kept, f"{model.__name__} has no text field to probe"
    assert [name for name, value in kept.items() if not all(map(_has_utf8_form, _texts(value)))] == []
