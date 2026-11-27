"""Shared builders for the speech-to-text tests: a voice config, a throw-away audio file, an HTTP stand-in and faster-whisper stand-ins."""

from __future__ import annotations

import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from app.channels.types import VoiceConfig


def stt_config(
    provider: str = "openai",
    api_key: str = "sk-test",
    local_model: str = "base",
    local_device: str = "auto",
    local_compute_type: str = "auto",
) -> VoiceConfig:
    return VoiceConfig(
        stt_enabled=True,
        stt_provider=provider,
        stt_api_key=api_key,
        stt_local_model=local_model,
        stt_local_device=local_device,
        stt_local_compute_type=local_compute_type,
    )


@dataclass
class FakeSegment:
    """Mimics faster_whisper TranscriptionSegment for testing."""

    text: str


@dataclass
class FakeTranscriptionInfo:
    """Mimics faster_whisper TranscriptionInfo for testing."""

    language: str
    duration: float


def audio_file(size: int = 8192, suffix: str = ".mp3") -> Path:
    """Create a temporary audio file with given size."""
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    tmp.write(b"\x00" * size)
    tmp.close()
    return Path(tmp.name)


@contextmanager
def mocked_http_client(response: httpx.Response) -> Iterator[AsyncMock]:
    """Make the providers' ``httpx.AsyncClient`` answer every POST with ``response``; yields the client to inspect."""
    with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as client_cls:
        client = AsyncMock()
        client.post.return_value = response
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client
        yield client


def mock_whisper_model() -> MagicMock:
    """Create a mock WhisperModel that returns fake segments + info."""
    model = MagicMock()
    model.transcribe.return_value = (
        [FakeSegment(text="Hello local"), FakeSegment(text=" world")],
        FakeTranscriptionInfo(language="en", duration=2.1),
    )
    return model
