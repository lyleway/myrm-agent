"""Which transcription model a provider runs by default, and how a model wants the spoken language named.

[INPUT]
- (none)

[OUTPUT]
- default_stt_model: the model a provider runs when the user chose none
- language_fields: the multipart field that names the spoken language, in the shape the model accepts

[POS]
Pure rules of the voice stt package, shared by the batch providers and the streaming endpoints so no caller repeats a
model name. Nothing is pinned in stored settings: an empty model setting always resolves here, so a provider that
retires its default only needs this file to change.
"""

from __future__ import annotations

_DEEPGRAM_DEFAULT = "nova-3"
_GROQ_DEFAULT = "whisper-large-v3"
_OPENAI_DEFAULT = "gpt-transcribe"
# A custom endpoint is a server of unknown make; this is the model name OpenAI-compatible servers recognise.
_COMPATIBLE_SERVER_DEFAULT = "whisper-1"

# The gpt-transcribe family takes a list of languages instead of a single `language`.
_LANGUAGE_LIST_MODEL_PREFIX = "gpt-transcribe"


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
