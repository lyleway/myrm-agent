"""Speech-to-text entry points: provider dispatch with local/cloud fallback, and remote audio download.

[INPUT]
- channels.types::VoiceConfig / STTResult (POS: voice configuration and transcription result)
- voice.stt.common::_MAX_AUDIO_SIZE_BYTES / _guess_extension (POS: audio size cap and file suffix lookup)
- voice.stt.local::_transcribe_local / is_local_available (POS: local provider and its availability)
- voice.stt.providers::_transcribe_openai_compatible / _transcribe_deepgram / _transcribe_xai (POS: cloud providers)

[OUTPUT]
- transcribe(): audio file or bytes -> STTResult (text + language + duration), best-effort, never raises
- download_audio(): remote audio URL -> temporary file path

[POS]
Inbound speech-to-text. Called by the router when an InboundMessage contains an AUDIO attachment; transcribes voice
content to text and injects it into the message. A failing primary provider falls back to local when installed (free),
a failing local provider falls back to the configured cloud API.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path

import httpx

from app.channels.types import STTResult, VoiceConfig
from app.channels.voice.stt.common import _MAX_AUDIO_SIZE_BYTES, _guess_extension
from app.channels.voice.stt.local import _transcribe_local, is_local_available
from app.channels.voice.stt.providers import (
    _transcribe_deepgram,
    _transcribe_openai_compatible,
    _transcribe_xai,
)

logger = logging.getLogger(__name__)


async def transcribe(audio_path: Path | None, config: VoiceConfig, audio_bytes: bytes | None = None) -> STTResult | None:
    """Transcribe an audio file or bytes using the configured STT provider.

    Fallback strategy (mirrors TTS pattern):
      - Primary provider fails → try local if available
      - Local provider fails → try first available cloud API

    Returns None if transcription fails (best-effort, never blocks message flow).
    """
    if audio_bytes is None:
        if audio_path is None or not audio_path.exists():
            logger.warning("STT: audio file not found: %s", audio_path)
            return None
        file_size = audio_path.stat().st_size
    else:
        file_size = len(audio_bytes)

    if file_size < 1024:
        logger.warning("STT: audio file too small (%d bytes), skipping", file_size)
        return None
    if file_size > _MAX_AUDIO_SIZE_BYTES:
        logger.warning("STT: audio file too large (%d bytes), skipping", file_size)
        return None

    provider = config.stt_provider.lower()

    try:
        if provider == "local":
            return await _transcribe_local(audio_path, config, audio_bytes)
        if provider in ("openai", "groq"):
            return await _transcribe_openai_compatible(audio_path, config, audio_bytes)
        if provider == "deepgram":
            return await _transcribe_deepgram(audio_path, config, audio_bytes)
        if provider == "xai":
            return await _transcribe_xai(audio_path, config, audio_bytes)
        logger.warning("STT: unknown provider '%s', trying OpenAI-compatible", provider)
        return await _transcribe_openai_compatible(audio_path, config, audio_bytes)
    except Exception:
        logger.exception("STT: primary provider '%s' failed", provider)

    # Fallback: if primary was cloud, try local; if primary was local, try cloud
    if provider != "local" and is_local_available():
        try:
            logger.warning("STT: falling back to local whisper (free)")
            return await _transcribe_local(audio_path, config, audio_bytes)
        except Exception:
            logger.exception("STT: local whisper fallback also failed")
    elif provider == "local" and config.stt_api_key:
        try:
            logger.warning("STT: local failed, falling back to cloud API")
            return await _transcribe_openai_compatible(audio_path, config, audio_bytes)
        except Exception:
            logger.exception("STT: cloud API fallback also failed")

    return None


async def download_audio(url: str, *, timeout: float = 30.0) -> Path | None:
    """Download a remote audio file to a temporary path.

    Returns None on failure.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            suffix = _guess_extension(url, resp.headers.get("content-type", ""))
            tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
            tmp.write(resp.content)
            tmp.close()
            return Path(tmp.name)
    except Exception:
        logger.exception("STT: failed to download audio from %s", url)
        return None
