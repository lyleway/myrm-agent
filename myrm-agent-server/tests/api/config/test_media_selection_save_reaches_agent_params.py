"""An image or video model saved through the settings routes reaches the media params of the agent.

[INPUT]
app.api.config.routes.crud (PUT /config/{key}, POST /config/sync)
app.core.channel_bridge.config_loader::load_user_configs
app.services.agent.params.media::_extract_media_generation_params

[OUTPUT]
Proof that the selection the settings screen saves is the selection the agent's image and video tools are built from.

[POS]
Real routes, real config store, the real loader with its cache and the real params extraction; only the LLM provider fallback
the loader insists on is stubbed. The screen saves a selection inside the whole personalSettings value, so a value schema that
does not name the selection keys drops them on save and the agent never gets the tool.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

import app.core.channel_bridge.model_resolver as model_resolver
from app.ai_agents import ImageGenerationParams, VideoGenerationParams
from app.core.channel_bridge.config_cache import invalidate_user_configs_cache
from app.core.channel_bridge.config_loader import load_user_configs
from app.database.connection import get_session, init_database
from app.database.models import ConfigAuditLog, UserConfig
from app.services.agent.params.media import _extract_media_generation_params
from tests.api.config.conftest import cleanup_shared_test_db
from tests.support.minimal_app import build_minimal_app

app = build_minimal_app(preset="config")
client = TestClient(app)

_IMAGE_MODEL = "gpt-image-2.6-sunburst"
_IMAGE_SELECTION: dict[str, object] = {
    "model": _IMAGE_MODEL,
    "fallbackModels": [],
    "defaultSize": "1024x1024",
    "timeoutSeconds": 120,
    "maxRetries": 1,
}
_VIDEO_SELECTION: dict[str, object] = {
    "provider": "volcengine",
    "model": "seedance-2-0",
    "fallbackProviders": [],
    "timeoutSeconds": 300,
    "maxRetries": 1,
}


def _saved_version() -> str | None:
    version = client.get("/api/v1/config/personalSettings").json()["version"]
    return None if version == "0" else version


def _save_with_put(value: dict[str, object]) -> None:
    response = client.put(
        "/api/v1/config/personalSettings", json={"value": value, "device_id": "d1", "expectedVersion": _saved_version()}
    )
    assert response.status_code == 200, response.text


def _save_with_sync(value: dict[str, object]) -> None:
    change = {"key": "personalSettings", "value": value, "expectedVersion": _saved_version(), "timestamp": 1}
    response = client.post("/api/v1/config/sync", json={"changes": [change], "deviceId": "d1"})
    assert response.status_code == 200, response.text
    assert response.json()["success"] is True, response.text


async def _clear_saved_settings() -> None:
    async with get_session() as session:
        await session.execute(delete(ConfigAuditLog))
        await session.execute(delete(UserConfig))
        await session.commit()
    invalidate_user_configs_cache()


async def _media_params() -> tuple[ImageGenerationParams | None, VideoGenerationParams | None]:
    """The image and video params an agent with both tools enabled is built with from what the loader reads now."""
    configs = await load_user_configs()
    image, video, _ = _extract_media_generation_params(
        configs.personal_settings_dict, configs.providers_dict, ["image_generation", "video_generation"], None
    )
    return image, video


@pytest.fixture(scope="module", autouse=True)
def _own_schema() -> Iterator[None]:
    """Earlier modules delete the shared database file, so this one creates its schema itself, as they do."""
    asyncio.run(init_database())
    yield
    cleanup_shared_test_db(Path(os.environ["MYRM_DATA_DIR"]) / "data.db")


@pytest.fixture(autouse=True)
async def _fresh_settings(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[None]:
    # The loader insists on a configured LLM provider; the media path under test does not depend on it.
    monkeypatch.setattr(
        model_resolver, "_fallback_model_from_providers", lambda providers, sandbox: SimpleNamespace(model="stub")
    )
    monkeypatch.setattr(model_resolver, "enrich_model_context_window", lambda model_cfg, providers: model_cfg)
    await _clear_saved_settings()
    yield
    await _clear_saved_settings()


@pytest.mark.parametrize("save", [_save_with_put, _save_with_sync], ids=["PUT /config/{key}", "POST /config/sync"])
async def test_the_saved_selections_are_what_the_agent_is_built_with(save: Callable[[dict[str, object]], None]) -> None:
    save({"imageGeneration": _IMAGE_SELECTION, "videoGeneration": _VIDEO_SELECTION})

    image, video = await _media_params()

    assert image is not None
    assert image.model == _IMAGE_MODEL
    assert video is not None
    assert (video.provider, video.model) == ("volcengine", "seedance-2-0")
    stored = client.get("/api/v1/config/personalSettings").json()["value"]
    assert (stored["imageGeneration"], stored["videoGeneration"]) == (_IMAGE_SELECTION, _VIDEO_SELECTION)


async def test_nothing_is_picked_for_a_user_who_saved_no_selection() -> None:
    _save_with_sync({"enableMemory": True})

    assert await _media_params() == (None, None)
