"""download_audio() and the extension / MIME lookups the transcription pipeline relies on."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.channels.voice.stt import download_audio
from app.channels.voice.stt.common import _guess_extension, _guess_mime


class TestDownloadAudio:
    @pytest.mark.asyncio
    async def test_download_success(self) -> None:
        mock_resp = httpx.Response(
            200,
            content=b"\x00" * 1024,
            headers={"content-type": "audio/mpeg"},
            request=httpx.Request("GET", "https://example.com/voice.mp3"),
        )

        with patch("app.channels.voice.stt.core.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_resp
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await download_audio("https://example.com/voice.mp3")

        assert result is not None
        assert result.exists()
        assert result.suffix == ".mp3"
        result.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_download_failure_returns_none(self) -> None:
        with patch("app.channels.voice.stt.core.httpx.AsyncClient") as mock_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_cls.return_value = mock_client

            result = await download_audio("https://example.com/voice.mp3")

        assert result is None


class TestSttDownloadAudio:
    @pytest.mark.asyncio
    async def test_successful_download(self) -> None:
        mock_resp = MagicMock()
        mock_resp.content = b"audio data"
        mock_resp.raise_for_status = MagicMock()
        mock_resp.headers = {"content-type": "audio/mpeg"}

        with patch("app.channels.voice.stt.core.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = AsyncMock(return_value=mock_resp)
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = instance

            result = await download_audio("https://example.com/audio.mp3")
            assert result is not None
            try:
                assert result.exists()
                assert result.read_bytes() == b"audio data"
            finally:
                result.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_download_failure_returns_none(self) -> None:
        with patch("app.channels.voice.stt.core.httpx.AsyncClient") as mock_client:
            instance = AsyncMock()
            instance.get = AsyncMock(side_effect=RuntimeError("network error"))
            instance.__aenter__ = AsyncMock(return_value=instance)
            instance.__aexit__ = AsyncMock(return_value=False)
            mock_client.return_value = instance

            result = await download_audio("https://example.com/audio.mp3")
            assert result is None


class TestGuessExtension:
    def test_from_url_path(self) -> None:
        assert _guess_extension("https://cdn.example.com/file.ogg", "") == ".ogg"
        assert _guess_extension("https://cdn.example.com/file.wav", "") == ".wav"
        assert _guess_extension("https://cdn.example.com/file.m4a", "") == ".m4a"

    def test_from_content_type(self) -> None:
        assert _guess_extension("https://cdn.example.com/file", "audio/ogg") == ".ogg"
        assert _guess_extension("https://cdn.example.com/file", "audio/wav") == ".wav"
        assert _guess_extension("https://cdn.example.com/file", "audio/webm") == ".webm"

    def test_default_mp3(self) -> None:
        assert _guess_extension("https://cdn.example.com/file", "application/octet-stream") == ".mp3"


class TestGuessExtensionContentType:
    def test_mp4_content_type(self) -> None:
        assert _guess_extension("https://cdn.example.com/file", "audio/mp4") == ".m4a"

    def test_m4a_content_type(self) -> None:
        assert _guess_extension("https://cdn.example.com/file", "audio/m4a") == ".m4a"

    def test_opus_content_type(self) -> None:
        assert _guess_extension("https://cdn.example.com/file", "audio/opus") == ".ogg"


class TestGuessMime:
    def test_known_extensions(self) -> None:
        assert _guess_mime(Path("test.mp3")) == "audio/mpeg"
        assert _guess_mime(Path("test.ogg")) == "audio/ogg"
        assert _guess_mime(Path("test.wav")) == "audio/wav"
        assert _guess_mime(Path("test.m4a")) == "audio/mp4"

    def test_unknown_extension_defaults_to_mpeg(self) -> None:
        assert _guess_mime(Path("test.xyz")) == "audio/mpeg"


class TestSttHelpers:
    def test_guess_extension_from_url(self) -> None:
        assert _guess_extension("https://x.com/audio.ogg", "") == ".ogg"
        assert _guess_extension("https://x.com/audio.wav", "") == ".wav"
        assert _guess_extension("https://x.com/audio.m4a", "") == ".m4a"

    def test_guess_extension_from_content_type(self) -> None:
        assert _guess_extension("https://x.com/audio", "audio/ogg") == ".ogg"
        assert _guess_extension("https://x.com/audio", "audio/wav") == ".wav"
        assert _guess_extension("https://x.com/audio", "audio/webm") == ".webm"

    def test_guess_extension_default(self) -> None:
        assert _guess_extension("https://x.com/audio", "unknown") == ".mp3"

    def test_guess_mime(self) -> None:
        assert _guess_mime(Path("audio.mp3")) == "audio/mpeg"
        assert _guess_mime(Path("audio.wav")) == "audio/wav"
        assert _guess_mime(Path("audio.ogg")) == "audio/ogg"
        assert _guess_mime(Path("audio.unknown")) == "audio/mpeg"
