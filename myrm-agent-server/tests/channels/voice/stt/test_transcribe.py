"""transcribe(): input validation, provider dispatch, in-memory audio, unknown providers and the local/cloud fallback."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.channels.types import STTResult, VoiceConfig
from app.channels.voice.stt import transcribe
from app.channels.voice.stt.local import _LocalWhisperManager
from tests.channels.voice.stt.stt_testkit import audio_file, mock_whisper_model, stt_config


class TestTranscribeValidation:
    @pytest.mark.asyncio
    async def test_returns_none_for_missing_file(self) -> None:
        result = await transcribe(Path("/nonexistent/audio.mp3"), stt_config())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_tiny_file(self) -> None:
        path = audio_file(size=512)
        try:
            result = await transcribe(path, stt_config())
            assert result is None
        finally:
            path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_returns_none_for_oversized_file(self) -> None:
        path = audio_file(size=1024)
        try:
            with patch("app.channels.voice.stt.core._MAX_AUDIO_SIZE_BYTES", 512):
                result = await transcribe(path, stt_config())
                assert result is None
        finally:
            path.unlink(missing_ok=True)


class TestTranscribeDispatch:
    @pytest.mark.asyncio
    async def test_missing_file_returns_none(self) -> None:
        result = await transcribe(Path("/nonexistent/audio.mp3"), stt_config())
        assert result is None

    @pytest.mark.asyncio
    async def test_too_small_file_returns_none(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(b"tiny")
        tmp.close()
        try:
            result = await transcribe(Path(tmp.name), stt_config())
            assert result is None
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_too_large_file_returns_none(self) -> None:
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(b"x" * (26 * 1024 * 1024))
        tmp.close()
        try:
            result = await transcribe(Path(tmp.name), stt_config())
            assert result is None
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_openai_provider_dispatch(self) -> None:
        config = stt_config("openai")
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(b"x" * 2048)
        tmp.close()
        try:
            with patch(
                "app.channels.voice.stt.core._transcribe_openai_compatible",
                new_callable=AsyncMock,
                return_value=STTResult(text="hello"),
            ) as mock_fn:
                result = await transcribe(Path(tmp.name), config)
                assert result is not None
                assert result.text == "hello"
                mock_fn.assert_called_once()
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_groq_provider_dispatch(self) -> None:
        config = stt_config("groq")
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(b"x" * 2048)
        tmp.close()
        try:
            with patch(
                "app.channels.voice.stt.core._transcribe_openai_compatible",
                new_callable=AsyncMock,
                return_value=STTResult(text="groq result"),
            ):
                result = await transcribe(Path(tmp.name), config)
                assert result is not None
                assert result.text == "groq result"
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_deepgram_provider_dispatch(self) -> None:
        config = stt_config("deepgram")
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(b"x" * 2048)
        tmp.close()
        try:
            with patch(
                "app.channels.voice.stt.core._transcribe_deepgram",
                new_callable=AsyncMock,
                return_value=STTResult(text="deepgram result"),
            ):
                result = await transcribe(Path(tmp.name), config)
                assert result is not None
                assert result.text == "deepgram result"
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_unknown_provider_falls_back_to_openai(self) -> None:
        config = stt_config("unknown")
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(b"x" * 2048)
        tmp.close()
        try:
            with patch(
                "app.channels.voice.stt.core._transcribe_openai_compatible",
                new_callable=AsyncMock,
                return_value=STTResult(text="fallback"),
            ):
                result = await transcribe(Path(tmp.name), config)
                assert result is not None
        finally:
            Path(tmp.name).unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_exception_returns_none(self) -> None:
        config = stt_config("openai")
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(b"x" * 2048)
        tmp.close()
        try:
            with patch(
                "app.channels.voice.stt.core._transcribe_openai_compatible",
                new_callable=AsyncMock,
                side_effect=RuntimeError("API error"),
            ):
                result = await transcribe(Path(tmp.name), config)
                assert result is None
        finally:
            Path(tmp.name).unlink(missing_ok=True)


class TestTranscribeAudioBytes:
    @pytest.mark.asyncio
    async def test_audio_bytes_too_small_returns_none(self) -> None:
        result = await transcribe(None, stt_config(), audio_bytes=b"\x00" * 512)
        assert result is None

    @pytest.mark.asyncio
    async def test_audio_bytes_openai_success(self) -> None:
        audio_data = b"\x00" * 8192
        response_body = {"text": "Bytes transcribed", "language": "en", "duration": 1.5}
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

            result = await transcribe(None, stt_config("openai"), audio_bytes=audio_data)

        assert result is not None
        assert result.text == "Bytes transcribed"

    @pytest.mark.asyncio
    async def test_audio_bytes_deepgram_success(self) -> None:
        audio_data = b"\x00" * 8192
        response_body = {
            "results": {
                "channels": [{"alternatives": [{"transcript": "Deepgram bytes"}]}],
            },
            "metadata": {"language": "en", "duration": 2.0},
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

            result = await transcribe(None, stt_config("deepgram"), audio_bytes=audio_data)

        assert result is not None
        assert result.text == "Deepgram bytes"


class TestTranscribeUnknownProvider:
    @pytest.mark.asyncio
    async def test_unknown_provider_falls_back_to_openai_compatible(self) -> None:
        path = audio_file()
        response_body = {"text": "Unknown handled", "language": "en"}
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
                result = await transcribe(
                    path,
                    VoiceConfig(
                        stt_enabled=True,
                        stt_provider="some_unknown_provider",
                        stt_api_key="sk-test",
                    ),
                )
            finally:
                path.unlink(missing_ok=True)

        assert result is not None
        assert result.text == "Unknown handled"


class TestFallback:
    @pytest.mark.asyncio
    async def test_cloud_fails_fallback_to_local(self) -> None:
        """When cloud provider fails and local is available, fallback to local."""
        path = audio_file()
        mock_model = mock_whisper_model()

        with (
            patch(
                "app.channels.voice.stt.core._transcribe_openai_compatible",
                new_callable=AsyncMock,
                side_effect=RuntimeError("API down"),
            ),
            patch(
                "app.channels.voice.stt.core.is_local_available",
                return_value=True,
            ),
            patch.object(_LocalWhisperManager, "get_model", new_callable=AsyncMock, return_value=mock_model),
        ):
            try:
                result = await transcribe(path, stt_config("openai"))
            finally:
                path.unlink(missing_ok=True)

        assert result is not None
        assert result.text == "Hello local world"

    @pytest.mark.asyncio
    async def test_local_fails_fallback_to_cloud(self) -> None:
        """When local provider fails and cloud API key is configured, fallback to cloud."""
        path = audio_file()
        response_body = {"text": "Cloud fallback", "language": "en"}

        mock_resp = httpx.Response(
            200,
            json=response_body,
            request=httpx.Request("POST", "https://api.openai.com/v1/audio/transcriptions"),
        )

        with (
            patch.object(
                _LocalWhisperManager,
                "get_model",
                new_callable=AsyncMock,
                side_effect=RuntimeError("Model load failed"),
            ),
            patch("app.channels.voice.stt.providers.httpx.AsyncClient") as mock_cls,
        ):
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            try:
                result = await transcribe(path, stt_config("local", api_key="sk-fallback"))
            finally:
                path.unlink(missing_ok=True)

        assert result is not None
        assert result.text == "Cloud fallback"

    @pytest.mark.asyncio
    async def test_local_fails_no_cloud_key_returns_none(self) -> None:
        """When local fails and no cloud API key, returns None."""
        path = audio_file()

        with patch.object(
            _LocalWhisperManager,
            "get_model",
            new_callable=AsyncMock,
            side_effect=RuntimeError("Model load failed"),
        ):
            try:
                result = await transcribe(path, stt_config("local", api_key=""))
            finally:
                path.unlink(missing_ok=True)

        assert result is None

    @pytest.mark.asyncio
    async def test_cloud_fails_no_local_returns_none(self) -> None:
        """When cloud fails and local is not available, returns None."""
        path = audio_file()

        with (
            patch(
                "app.channels.voice.stt.core._transcribe_openai_compatible",
                new_callable=AsyncMock,
                side_effect=RuntimeError("API down"),
            ),
            patch(
                "app.channels.voice.stt.core.is_local_available",
                return_value=False,
            ),
        ):
            try:
                result = await transcribe(path, stt_config("openai"))
            finally:
                path.unlink(missing_ok=True)

        assert result is None
