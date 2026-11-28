"""Real speech for tests that have no microphone: a system voice reading a fixed sentence, and the model that understands it.

The voice integration tests and the dictation browser E2E both need audio a speech-to-text model can actually transcribe, so they
share one sentence, one way of producing it and one answer to "is the key-less local model on this host". A host without a
speech synthesizer or without the model is reported to the caller, which decides whether that skips its test: no test downloads
a model.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

SPEECH_TEXT = "Please remember to buy milk and eggs tomorrow morning."
# Words a transcription of ``SPEECH_TEXT`` has to contain; the rest of the sentence is allowed to vary between model runs.
SPOKEN_WORDS = ("milk", "eggs")

_SPEECH_VOICE = "Samantha"
_SPEECH_SYNTHESIS_TIMEOUT_SECONDS = 120.0
_WHISPER_BASE_CACHE_DIR = "models--Systran--faster-whisper-base"


def synthesize_speech(path: Path) -> bytes | None:
    """The 16 kHz mono WAV of a system voice saying ``SPEECH_TEXT``, or None when the host has no speech synthesizer."""
    say = shutil.which("say")
    if say is None:
        return None
    command = [say, "-v", _SPEECH_VOICE, "-o", str(path), "--file-format=WAVE", "--data-format=LEI16@16000", SPEECH_TEXT]
    done = subprocess.run(command, capture_output=True, check=False, timeout=_SPEECH_SYNTHESIS_TIMEOUT_SECONDS)
    return path.read_bytes() if done.returncode == 0 and path.exists() else None


def whisper_base_model_cached() -> bool:
    """Whether the faster-whisper ``base`` model the key-less ``local`` provider loads is already on this host."""
    from huggingface_hub.constants import HF_HUB_CACHE

    return (Path(HF_HUB_CACHE) / _WHISPER_BASE_CACHE_DIR).is_dir()
