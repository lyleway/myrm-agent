"""Text-to-speech through the public facade: provider dispatch and fallback, request builders and the temp-file writer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.channels.voice.tts import (
    _elevenlabs_request,
    _fish_audio_request,
    _minimax_request,
    _resolve_base_url,
    _write_temp,
    synthesize,
)
from tests.channels.voice.voice_testkit import voice_config


class TestTtsSynthesize:
    @pytest.mark.asyncio
    async def test_short_text_returns_none(self) -> None:
        config = voice_config()
        result = await synthesize("Hi", config)
        assert result is None

    @pytest.mark.asyncio
    async def test_long_text_returns_none(self) -> None:
        config = voice_config(tts_max_length=50)
        result = await synthesize("x" * 100, config)
        assert result is None

    @pytest.mark.asyncio
    async def test_edge_provider_dispatch(self) -> None:
        config = voice_config(tts_provider="edge")
        with patch(
            "app.channels.voice.tts.core._synthesize_edge",
            new_callable=AsyncMock,
            return_value=Path("/tmp/edge.mp3"),
        ) as mock_edge:
            result = await synthesize("Hello world test text", config)
            assert result == Path("/tmp/edge.mp3")
            mock_edge.assert_called_once()

    @pytest.mark.asyncio
    async def test_api_provider_dispatch(self) -> None:
        config = voice_config(tts_provider="elevenlabs")
        with patch(
            "app.channels.voice.tts.core._synthesize_api",
            new_callable=AsyncMock,
            return_value=Path("/tmp/elevenlabs.mp3"),
        ) as mock_api:
            result = await synthesize("Hello world test text", config)
            assert result == Path("/tmp/elevenlabs.mp3")
            mock_api.assert_called_once()

    @pytest.mark.asyncio
    async def test_primary_failure_falls_back_to_edge(self) -> None:
        config = voice_config(tts_provider="elevenlabs")
        with (
            patch(
                "app.channels.voice.tts.core._synthesize_api",
                new_callable=AsyncMock,
                side_effect=RuntimeError("API down"),
            ),
            patch(
                "app.channels.voice.tts.core.is_edge_tts_available",
                return_value=True,
            ),
            patch(
                "app.channels.voice.tts.core._synthesize_edge",
                new_callable=AsyncMock,
                return_value=Path("/tmp/fallback.mp3"),
            ) as mock_edge,
        ):
            result = await synthesize("Hello world test text", config)
            assert result == Path("/tmp/fallback.mp3")
            mock_edge.assert_called_once()

    @pytest.mark.asyncio
    async def test_edge_failure_no_double_fallback(self) -> None:
        config = voice_config(tts_provider="edge")
        with patch(
            "app.channels.voice.tts.core._synthesize_edge",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Edge down"),
        ):
            result = await synthesize("Hello world test text", config)
            assert result is None

    @pytest.mark.asyncio
    async def test_both_fail_returns_none(self) -> None:
        config = voice_config(tts_provider="elevenlabs")
        with (
            patch(
                "app.channels.voice.tts.core._synthesize_api",
                new_callable=AsyncMock,
                side_effect=RuntimeError("API down"),
            ),
            patch(
                "app.channels.voice.tts.core.is_edge_tts_available",
                return_value=True,
            ),
            patch(
                "app.channels.voice.tts.core._synthesize_edge",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Edge down"),
            ),
        ):
            result = await synthesize("Hello world test text", config)
            assert result is None

    @pytest.mark.asyncio
    async def test_summary_triggered_for_long_text(self) -> None:
        config = voice_config(
            tts_provider="edge",
            tts_summary_enabled=True,
            tts_summary_threshold=20,
        )
        with (
            patch(
                "app.channels.voice.tts.core._summarize_for_tts",
                new_callable=AsyncMock,
                return_value="short summary",
            ) as mock_summary,
            patch(
                "app.channels.voice.tts.core._synthesize_edge",
                new_callable=AsyncMock,
                return_value=Path("/tmp/edge.mp3"),
            ) as mock_edge,
        ):
            await synthesize("A" * 100, config)
            mock_summary.assert_called_once()
            called_text = mock_edge.call_args[0][0]
            assert called_text == "short summary"


class TestTtsRequestBuilders:
    def test_elevenlabs_request(self) -> None:
        config = voice_config(tts_api_key="el-test")
        url, headers, params, body = _elevenlabs_request(config, "Hello", stream=False)
        assert "text-to-speech" in url
        assert headers["xi-api-key"] == "el-test"

    def test_fish_audio_request(self) -> None:
        config = voice_config(tts_api_key="fa-test")
        url, headers, body = _fish_audio_request(config, "Hello", "mp3")
        assert "/tts" in url
        assert body["format"] == "mp3"

    def test_minimax_request(self) -> None:
        config = voice_config(tts_api_key="mm-test")
        url, headers, body = _minimax_request(config, "Hello", stream=False)
        assert "/t2a_v2" in url
        assert body["model"] == "speech-2.8-hd"

    def test_resolve_base_url_voicebox_custom(self) -> None:
        config = voice_config(tts_base_url="https://custom.com/v1/")
        assert _resolve_base_url(config, "voicebox") == "https://custom.com/v1"

    def test_resolve_base_url_voicebox_default(self) -> None:
        assert _resolve_base_url(voice_config(), "voicebox") == "http://127.0.0.1:8000"

    def test_resolve_base_url_non_voicebox_ignores_custom(self) -> None:
        config = voice_config(tts_base_url="https://custom.com/v1/")
        assert _resolve_base_url(config, "elevenlabs") == "https://api.elevenlabs.io"


class TestTtsWriteTemp:
    def test_write_temp(self) -> None:
        path = _write_temp(b"test data", ".mp3")
        try:
            assert path.exists()
            assert path.read_bytes() == b"test data"
            assert path.suffix == ".mp3"
        finally:
            path.unlink(missing_ok=True)
