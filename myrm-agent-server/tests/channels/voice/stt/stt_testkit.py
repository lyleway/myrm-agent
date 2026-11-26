"""Shared builders for the speech-to-text tests: a voice config, a throw-away audio file and faster-whisper stand-ins."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import MagicMock

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


def mock_whisper_model() -> MagicMock:
    """Create a mock WhisperModel that returns fake segments + info."""
    model = MagicMock()
    model.transcribe.return_value = (
        [FakeSegment(text="Hello local"), FakeSegment(text=" world")],
        FakeTranscriptionInfo(language="en", duration=2.1),
    )
    return model
