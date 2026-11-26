"""Cloud speech-to-text providers: OpenAI-compatible (OpenAI, Groq, custom endpoints), Deepgram and xAI.

[INPUT]
- channels.types::VoiceConfig / STTResult (POS: voice configuration and transcription result)
- voice.stt.common::_guess_mime (POS: MIME type of an audio file)
- voice.stt.models::default_stt_model / language_fields (POS: model default and language field per model family)

[OUTPUT]
- _transcribe_openai_compatible / _transcribe_deepgram / _transcribe_xai: one HTTP transcription per provider

[POS]
Network half of the voice stt package: each function turns one audio input into an STTResult or raises; core decides
what to fall back to.
"""

from __future__ import annotations

import logging
from pathlib import Path

import httpx

from app.channels.types import STTResult, VoiceConfig
from app.channels.voice.stt.common import _guess_mime
from app.channels.voice.stt.models import default_stt_model, language_fields

logger = logging.getLogger(__name__)

_OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"
_GROQ_TRANSCRIPTION_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_DEEPGRAM_LISTEN_URL = "https://api.deepgram.com/v1/listen"


async def _transcribe_openai_compatible(
    audio_path: Path | None,
    config: VoiceConfig,
    audio_bytes: bytes | None = None,
) -> STTResult:
    """OpenAI / Groq / custom OpenAI-compatible endpoint transcription (same API format)."""
    provider = config.stt_provider.lower()
    if config.stt_base_url:
        base_url = config.stt_base_url.rstrip("/") + "/audio/transcriptions"
    elif provider == "groq":
        base_url = _GROQ_TRANSCRIPTION_URL
    else:
        base_url = _OPENAI_TRANSCRIPTION_URL
    model = config.stt_model or default_stt_model(provider, custom_endpoint=bool(config.stt_base_url))

    async with httpx.AsyncClient(timeout=60.0) as client:
        data = {"model": model, "response_format": "json", **language_fields(model, config.stt_language)}

        if audio_bytes is not None:
            resp = await client.post(
                base_url,
                headers={"Authorization": f"Bearer {config.stt_api_key}"},
                files={"file": ("audio.wav", audio_bytes, "audio/wav")},
                data=data,
            )
            resp.raise_for_status()
        else:
            assert audio_path is not None
            with audio_path.open("rb") as f:
                resp = await client.post(
                    base_url,
                    headers={"Authorization": f"Bearer {config.stt_api_key}"},
                    files={"file": (audio_path.name, f, _guess_mime(audio_path))},
                    data=data,
                )
                resp.raise_for_status()

    body = resp.json()
    text = body.get("text", "").strip()
    if not text:
        raise ValueError("Empty transcript returned")

    logger.info(
        "STT: %s transcription ok (model=%s, chars=%d)",
        provider,
        model,
        len(text),
    )
    return STTResult(
        text=text,
        language=body.get("language"),
        duration=body.get("duration"),
    )


async def _transcribe_deepgram(
    audio_path: Path | None,
    config: VoiceConfig,
    audio_bytes: bytes | None = None,
) -> STTResult:
    """Deepgram transcription API."""
    model = config.stt_model or default_stt_model("deepgram")
    if audio_bytes is not None:
        mime, payload = "audio/wav", audio_bytes
    else:
        assert audio_path is not None
        mime, payload = _guess_mime(audio_path), audio_path.read_bytes()

    params: dict[str, str] = {"model": model, "smart_format": "true"}
    if config.stt_language:
        params["language"] = config.stt_language

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            _DEEPGRAM_LISTEN_URL,
            headers={
                "Authorization": f"Token {config.stt_api_key}",
                "Content-Type": mime,
            },
            params=params,
            content=payload,
        )
        resp.raise_for_status()

    body = resp.json()
    channels = body.get("results", {}).get("channels", [])
    if not channels:
        raise ValueError("No channels in Deepgram response")

    alternatives = channels[0].get("alternatives", [])
    if not alternatives:
        raise ValueError("No alternatives in Deepgram response")

    text = alternatives[0].get("transcript", "").strip()
    if not text:
        raise ValueError("Empty transcript returned")

    metadata = body.get("metadata", {})
    logger.info(
        "STT: deepgram transcription ok (model=%s, chars=%d)",
        model,
        len(text),
    )
    return STTResult(
        text=text,
        language=metadata.get("language"),
        duration=metadata.get("duration"),
    )


_XAI_STT_URL = "https://api.x.ai/v1/stt"


def _resolve_xai_stt_api_key(config: VoiceConfig) -> str:
    """Resolve xAI STT API key: explicit config first, then OAuth token fallback."""
    if config.stt_api_key:
        return config.stt_api_key
    try:
        from myrm_agent_harness.agent.security import user_credentials_ctx

        from app.services.agent.session_credential_assembler import XAI_ISSUER

        for cred in user_credentials_ctx.get():
            if cred.issuer == XAI_ISSUER and cred.token.strip():
                return cred.token
    except Exception:
        pass
    return config.stt_api_key


async def _transcribe_xai(
    audio_path: Path | None,
    config: VoiceConfig,
    audio_bytes: bytes | None = None,
) -> STTResult:
    """xAI Grok STT via POST /v1/stt (multipart/form-data).

    Supports Inverse Text Normalization and diarization.
    Non-OpenAI-compatible API — requires dedicated implementation.
    """
    api_key = _resolve_xai_stt_api_key(config)
    base_url = config.stt_base_url.rstrip("/") if config.stt_base_url else _XAI_STT_URL.rsplit("/", 1)[0]
    url = f"{base_url}/stt"

    data: dict[str, str] = {}
    if config.stt_language:
        data["language"] = config.stt_language
    data["format"] = "true"

    async with httpx.AsyncClient(timeout=120.0) as client:
        if audio_bytes is not None:
            resp = await client.post(
                url,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.wav", audio_bytes, "audio/wav")},
                data=data,
            )
            resp.raise_for_status()
        else:
            assert audio_path is not None
            with audio_path.open("rb") as f:
                resp = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {api_key}"},
                    files={"file": (audio_path.name, f, _guess_mime(audio_path))},
                    data=data,
                )
                resp.raise_for_status()

    body = resp.json()
    text = body.get("text", "").strip()
    if not text:
        raise ValueError("Empty transcript returned from xAI STT")

    logger.info(
        "STT: xai transcription ok (chars=%d, lang=%s)",
        len(text),
        body.get("language"),
    )
    return STTResult(
        text=text,
        language=body.get("language"),
        duration=body.get("duration"),
    )
