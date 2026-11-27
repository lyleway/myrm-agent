"""The STT upload and TTS read-aloud routes read the voice settings alone, so a user who set up voice but no LLM provider is served."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from myrm_agent_harness.api.config import ConfigIncompleteError

from app.api.stt.router import _load_user_voice_config
from app.api.tts.router import TTSRequest, _resolve_voice_config

_VOICE_ONLY_LOADER = "app.core.channel_bridge.config_loader.load_voice_config_only"
_FULL_LOADER = "app.core.channel_bridge.config_loader.load_user_configs"


@pytest.fixture
def no_llm_provider() -> Iterator[AsyncMock]:
    """``load_user_configs`` as it answers a user without any LLM provider: it refuses."""
    refusal = ConfigIncompleteError({"en": "Please configure at least one LLM provider."}, "no default model", [])
    with patch(_FULL_LOADER, new_callable=AsyncMock, side_effect=refusal) as loader:
        yield loader


class TestSttUpload:
    async def test_the_voice_settings_are_read_without_a_default_model(self, no_llm_provider: AsyncMock) -> None:
        with patch(_VOICE_ONLY_LOADER, new_callable=AsyncMock, return_value={"sttEnabled": True, "sttProvider": "local"}):
            voice_config = await _load_user_voice_config("sandbox")

        assert voice_config is not None
        assert (voice_config.stt_enabled, voice_config.stt_provider) == (True, "local")
        no_llm_provider.assert_not_awaited()

    async def test_unreadable_voice_settings_count_as_not_configured(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            caplog.at_level(logging.ERROR, logger="app.api.stt.router"),
            patch(_VOICE_ONLY_LOADER, new_callable=AsyncMock, side_effect=RuntimeError("database is locked")),
        ):
            assert await _load_user_voice_config("sandbox") is None

        assert "Failed to load voice config" in caplog.text


class TestTtsReadAloud:
    async def test_the_voice_settings_are_read_without_a_default_model(self, no_llm_provider: AsyncMock) -> None:
        settings = {"sttEnabled": False, "ttsMode": "off", "ttsProvider": "edge"}
        with patch(_VOICE_ONLY_LOADER, new_callable=AsyncMock, return_value=settings):
            voice_config = await _resolve_voice_config("sandbox", TTSRequest(text="Hello"))

        assert voice_config.tts_provider == "edge"
        no_llm_provider.assert_not_awaited()

    async def test_unreadable_voice_settings_are_reported_as_not_found(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            caplog.at_level(logging.ERROR, logger="app.api.tts.router"),
            patch(_VOICE_ONLY_LOADER, new_callable=AsyncMock, side_effect=RuntimeError("database is locked")),
            pytest.raises(HTTPException) as refusal,
        ):
            await _resolve_voice_config("sandbox", TTSRequest(text="Hello"))

        assert refusal.value.status_code == 400
        assert "Voice config not found" in str(refusal.value.detail)
        assert "Failed to load voice config" in caplog.text
