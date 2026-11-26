"""The OpenAI-compatible and Deepgram providers against a mocked HTTP client."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.channels.types import VoiceConfig
from app.channels.voice.stt import transcribe
from tests.channels.voice.stt.stt_testkit import audio_file, stt_config


class TestTranscribeOpenAI:
    @pytest.mark.asyncio
    async def test_openai_transcription_success(self) -> None:
        path = audio_file()
        response_body = {"text": "Hello world", "language": "en", "duration": 2.5}

        mock_resp = httpx.Response(
            200,
            json=response_body,
            request=httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions"),
        )

        with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            try:
                result = await transcribe(path, stt_config("openai"))
            finally:
                path.unlink(missing_ok=True)

        assert result is not None
        assert result.text == "Hello world"
        assert result.language == "en"
        assert result.duration == 2.5

    @pytest.mark.asyncio
    async def test_groq_uses_correct_model(self) -> None:
        path = audio_file()
        response_body = {"text": "Test", "language": "en"}

        mock_resp = httpx.Response(
            200,
            json=response_body,
            request=httpx.Request("POST", "https://api.groq.com/openai/v1/audio/transcriptions"),
        )

        with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            try:
                result = await transcribe(path, stt_config("groq"))
            finally:
                path.unlink(missing_ok=True)

        assert result is not None
        assert result.text == "Test"
        call_kwargs = mock_client.post.call_args
        assert "groq.com" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_returns_none_on_api_error(self) -> None:
        path = audio_file()

        with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.HTTPStatusError(
                "500",
                request=httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions"),
                response=httpx.Response(500),
            )
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            try:
                result = await transcribe(path, stt_config())
            finally:
                path.unlink(missing_ok=True)

        assert result is None


class TestOpenAIEdgeCases:
    @pytest.mark.asyncio
    async def test_openai_with_language_config(self) -> None:
        path = audio_file()
        response_body = {"text": "Bonjour", "language": "fr", "duration": 1.2}
        mock_resp = httpx.Response(
            200,
            json=response_body,
            request=httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions"),
        )

        with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            config = VoiceConfig(
                stt_enabled=True,
                stt_provider="openai",
                stt_api_key="sk-test",
                stt_language="fr",
            )
            try:
                result = await transcribe(path, config)
            finally:
                path.unlink(missing_ok=True)

        assert result is not None
        assert result.text == "Bonjour"

    @pytest.mark.asyncio
    async def test_openai_empty_transcript_returns_none(self) -> None:
        path = audio_file()
        response_body = {"text": "  ", "language": "en"}
        mock_resp = httpx.Response(
            200,
            json=response_body,
            request=httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions"),
        )

        with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            try:
                result = await transcribe(path, stt_config("openai"))
            finally:
                path.unlink(missing_ok=True)

        assert result is None


class TestTranscribeDeepgram:
    @pytest.mark.asyncio
    async def test_deepgram_transcription_success(self) -> None:
        path = audio_file()
        response_body = {
            "results": {
                "channels": [
                    {
                        "alternatives": [{"transcript": "Deepgram test"}],
                    }
                ],
            },
            "metadata": {"language": "en", "duration": 3.0},
        }

        mock_resp = httpx.Response(
            200,
            json=response_body,
            request=httpx.Request("POST", "https://api.deepgram.com/v1/listen"),
        )

        with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            try:
                result = await transcribe(path, stt_config("deepgram"))
            finally:
                path.unlink(missing_ok=True)

        assert result is not None
        assert result.text == "Deepgram test"
        assert result.language == "en"


class TestDeepgramEdgeCases:
    @pytest.mark.asyncio
    async def test_deepgram_no_channels_returns_none(self) -> None:
        path = audio_file()
        response_body = {"results": {"channels": []}, "metadata": {}}
        mock_resp = httpx.Response(
            200,
            json=response_body,
            request=httpx.Request("POST", "https://api.deepgram.com/v1/listen"),
        )

        with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            try:
                result = await transcribe(path, stt_config("deepgram"))
            finally:
                path.unlink(missing_ok=True)

        assert result is None

    @pytest.mark.asyncio
    async def test_deepgram_no_alternatives_returns_none(self) -> None:
        path = audio_file()
        response_body = {
            "results": {"channels": [{"alternatives": []}]},
            "metadata": {},
        }
        mock_resp = httpx.Response(
            200,
            json=response_body,
            request=httpx.Request("POST", "https://api.deepgram.com/v1/listen"),
        )

        with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            try:
                result = await transcribe(path, stt_config("deepgram"))
            finally:
                path.unlink(missing_ok=True)

        assert result is None

    @pytest.mark.asyncio
    async def test_deepgram_empty_transcript_returns_none(self) -> None:
        path = audio_file()
        response_body = {
            "results": {"channels": [{"alternatives": [{"transcript": "  "}]}]},
            "metadata": {},
        }
        mock_resp = httpx.Response(
            200,
            json=response_body,
            request=httpx.Request("POST", "https://api.deepgram.com/v1/listen"),
        )

        with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            try:
                result = await transcribe(path, stt_config("deepgram"))
            finally:
                path.unlink(missing_ok=True)

        assert result is None

    @pytest.mark.asyncio
    async def test_deepgram_with_language_param(self) -> None:
        path = audio_file()
        response_body = {
            "results": {
                "channels": [{"alternatives": [{"transcript": "Hola mundo"}]}],
            },
            "metadata": {"language": "es", "duration": 1.0},
        }
        mock_resp = httpx.Response(
            200,
            json=response_body,
            request=httpx.Request("POST", "https://api.deepgram.com/v1/listen"),
        )

        with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            config = VoiceConfig(
                stt_enabled=True,
                stt_provider="deepgram",
                stt_api_key="dg-test",
                stt_language="es",
            )
            try:
                result = await transcribe(path, config)
            finally:
                path.unlink(missing_ok=True)

        assert result is not None
        assert result.text == "Hola mundo"
