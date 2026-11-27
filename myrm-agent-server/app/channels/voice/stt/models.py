"""Default transcription models, the language field each model takes, and the live Deepgram address.

[INPUT]
- (none)

[OUTPUT]
- default_stt_model: the model a provider runs when the user chose none
- language_fields: the multipart field that names the spoken language, in the shape the model accepts
- deepgram_stream_url: the Deepgram live-transcription address for browser audio, default model applied

[POS]
Pure rules of the voice stt package, shared by the batch providers and the streaming endpoints so no transcription path
repeats a default model name or the live Deepgram address. Nothing is pinned in stored settings: an empty model setting
always resolves here, so a provider that retires its default only needs this file to change.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import quote

_DEEPGRAM_DEFAULT = "nova-3"
_GROQ_DEFAULT = "whisper-large-v3"
_OPENAI_DEFAULT = "gpt-transcribe"
# A custom endpoint is a server of unknown make; this is the model name OpenAI-compatible servers recognise.
_COMPATIBLE_SERVER_DEFAULT = "whisper-1"

# The gpt-transcribe family takes a list of languages instead of a single `language`.
_LANGUAGE_LIST_MODEL_PREFIX = "gpt-transcribe"

_DEEPGRAM_STREAM_URL = "wss://api.deepgram.com/v1/listen"
# Boost Deepgram gives a key term (`keywords=term:boost`).
_KEYTERM_BOOST = 2


def default_stt_model(provider: str, *, custom_endpoint: bool = False) -> str:
    """Return the model ``provider`` transcribes with when the settings name none."""
    if provider == "deepgram":
        return _DEEPGRAM_DEFAULT
    if provider == "groq":
        return _GROQ_DEFAULT
    return _COMPATIBLE_SERVER_DEFAULT if custom_endpoint else _OPENAI_DEFAULT


def language_fields(model: str, language: str | None) -> dict[str, str | list[str]]:
    """Return the form field naming the spoken ``language``, empty when none is configured.

    A ``gpt-transcribe`` model takes ``languages[]`` in place of the single ``language`` field (the two are never sent
    together); its documentation lists lower-case codes only.
    """
    if not language:
        return {}
    if model.startswith(_LANGUAGE_LIST_MODEL_PREFIX):
        return {"languages[]": [language.lower()]}
    return {"language": language}


def deepgram_stream_url(model: str, language: str | None, keyterms: Iterable[str]) -> str:
    """Return the Deepgram live-transcription URL for browser audio (opus, 48 kHz) with interim results.

    An empty ``model`` runs the Deepgram default; blank key terms are skipped.
    """
    params = [
        ("model", model or default_stt_model("deepgram")),
        ("smart_format", "true"),
        ("interim_results", "true"),
        ("endpointing", "300"),
        ("encoding", "opus"),
        ("sample_rate", "48000"),
    ]
    if language:
        params.append(("language", language))
    for term in keyterms:
        stripped = term.strip()
        if stripped:
            params.append(("keywords", f"{stripped}:{_KEYTERM_BOOST}"))
    query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params)
    return f"{_DEEPGRAM_STREAM_URL}?{query}"
