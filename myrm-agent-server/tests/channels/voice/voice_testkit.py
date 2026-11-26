"""Shared builder for the voice tests: a combined speech-to-text and text-to-speech config with working defaults."""

from __future__ import annotations

from app.channels.types import TTSMode, VoiceConfig


def voice_config(**overrides: object) -> VoiceConfig:
    defaults: dict[str, object] = {
        "stt_enabled": True,
        "stt_provider": "openai",
        "stt_api_key": "test-key",
        "tts_mode": TTSMode.ALWAYS,
        "tts_provider": "edge",
        "tts_api_key": "test-key",
        "tts_max_length": 4000,
    }
    defaults.update(overrides)
    return VoiceConfig(**defaults)  # type: ignore[arg-type]
