"""Audio size cap and file-type lookups shared by the speech-to-text modules.

[INPUT]
- (none)

[OUTPUT]
- _MAX_AUDIO_SIZE_BYTES / _SUPPORTED_AUDIO_EXTENSIONS / _MIME_MAP: what the pipeline accepts and how it labels it
- _guess_extension / _guess_mime: file suffix from a URL or Content-Type, MIME type from a path

[POS]
Leaf of the voice stt package: core and providers import it, it imports nothing from the package.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

_SUPPORTED_AUDIO_EXTENSIONS = frozenset(
    {
        ".mp3",
        ".mp4",
        ".mpeg",
        ".mpga",
        ".m4a",
        ".wav",
        ".webm",
        ".ogg",
        ".opus",
        ".flac",
    }
)

_MAX_AUDIO_SIZE_BYTES = 25 * 1024 * 1024  # 25 MB

_MIME_MAP: dict[str, str] = {
    ".mp3": "audio/mpeg",
    ".mp4": "audio/mp4",
    ".m4a": "audio/mp4",
    ".wav": "audio/wav",
    ".webm": "audio/webm",
    ".ogg": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".mpeg": "audio/mpeg",
    ".mpga": "audio/mpeg",
}


def _guess_extension(url: str, content_type: str) -> str:
    """Guess file extension from URL or Content-Type."""
    path = urlparse(url).path.lower()
    for ext in _SUPPORTED_AUDIO_EXTENSIONS:
        if path.endswith(ext):
            return ext

    ct = content_type.lower()
    if "ogg" in ct or "opus" in ct:
        return ".ogg"
    if "mp4" in ct or "m4a" in ct:
        return ".m4a"
    if "wav" in ct:
        return ".wav"
    if "webm" in ct:
        return ".webm"
    return ".mp3"


def _guess_mime(audio_path: Path) -> str:
    """Guess MIME type from file extension."""
    return _MIME_MAP.get(audio_path.suffix.lower(), "audio/mpeg")
