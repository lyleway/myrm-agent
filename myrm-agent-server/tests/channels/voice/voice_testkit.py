"""Shared builder for the voice tests: a combined speech-to-text and text-to-speech config with working defaults."""

from __future__ import annotations

from dataclasses import replace
from typing import TypedDict, Unpack

from app.channels.types import TTSMode, VoiceConfig


class _VoiceOverrides(TypedDict, total=False):
    """The settings the voice tests vary; every other field keeps the working default."""

    stt_enabled: bool
    tts_mode: TTSMode
    tts_provider: str
    tts_api_key: str
    tts_base_url: str
    tts_max_length: int
    tts_summary_enabled: bool
    tts_summary_threshold: int


def voice_config(**overrides: Unpack[_VoiceOverrides]) -> VoiceConfig:
    base = VoiceConfig(
        stt_enabled=True,
        stt_provider="openai",
        stt_api_key="test-key",
        tts_mode=TTSMode.ALWAYS,
        tts_provider="edge",
        tts_api_key="test-key",
        tts_max_length=4000,
    )
    return replace(base, **overrides)
