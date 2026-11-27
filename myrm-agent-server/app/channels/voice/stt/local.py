"""Local faster-whisper transcription: the process-wide model manager, availability checks and the provider itself.

[INPUT]
- channels.types::VoiceConfig / STTResult (POS: voice configuration and transcription result)

[OUTPUT]
- LOCAL_STT_INSTALL_HINT: the command that installs the optional faster-whisper dependency
- is_local_available / local_stt_unavailable_detail / get_local_status: availability and model status for the /stt APIs
- _LocalWhisperManager / _whisper_manager: lazily loaded, idle-unloading faster-whisper model
- _transcribe_local: the local provider, called by core

[POS]
Free, key-less provider of the voice stt package; imports nothing from its siblings.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.channels.types import STTResult, VoiceConfig

if TYPE_CHECKING:
    from faster_whisper import WhisperModel
    from faster_whisper.transcribe import Segment, TranscriptionInfo

logger = logging.getLogger(__name__)

LOCAL_STT_INSTALL_HINT = "uv sync --extra local-stt"


class _LocalWhisperManager:
    """Process-level singleton for the faster-whisper model.

    Loads the model lazily on first transcription call and reuses it
    across subsequent calls. Supports configurable idle unloading to free
    VRAM/RAM when sharing hardware with local LLM instances.
    """

    def __init__(self, idle_unload_seconds: float = 30.0) -> None:
        self._model: WhisperModel | None = None
        self._model_size: str = ""
        self._device: str = ""
        self._compute_type: str = ""
        self._lock = asyncio.Lock()
        self._idle_unload_seconds = idle_unload_seconds
        self._unload_timer: asyncio.TimerHandle | None = None

    def _resolve_device(self, requested: str) -> str:
        """Resolve 'auto' to actual device."""
        if requested != "auto":
            return requested
        try:
            import torch

            return "cuda" if torch.cuda.is_available() else "cpu"
        except ImportError:
            return "cpu"

    def _resolve_compute_type(self, requested: str, device: str) -> str:
        """Resolve 'auto' to optimal compute type for the device."""
        if requested != "auto":
            return requested
        return "float16" if device == "cuda" else "int8"

    def _schedule_unload(self) -> None:
        """Schedule automatic model unloading after idle timeout."""
        if self._idle_unload_seconds <= 0:
            return
        if self._unload_timer is not None:
            self._unload_timer.cancel()
        loop = asyncio.get_running_loop()
        self._unload_timer = loop.call_later(self._idle_unload_seconds, self.unload)

    def unload(self) -> None:
        """Unload Whisper model from memory/VRAM immediately."""
        if self._model is not None:
            logger.info("STT: unloading local whisper model to free memory/VRAM")
            self._model = None
            self._model_size = ""
            self._device = ""
            self._compute_type = ""
            import gc

            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass
        if self._unload_timer is not None:
            self._unload_timer.cancel()
            self._unload_timer = None

    async def get_model(self, config: VoiceConfig) -> WhisperModel:
        """Get or load the WhisperModel, reloading if config changed."""
        async with self._lock:
            if self._unload_timer is not None:
                self._unload_timer.cancel()
                self._unload_timer = None

            model_size = config.stt_local_model or "base"
            device = self._resolve_device(config.stt_local_device)
            compute_type = self._resolve_compute_type(config.stt_local_compute_type, device)

            if (
                self._model is not None
                and self._model_size == model_size
                and self._device == device
                and self._compute_type == compute_type
            ):
                return self._model

            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise ImportError(f"faster-whisper is not installed. Install: {LOCAL_STT_INSTALL_HINT}") from exc

            loop = asyncio.get_running_loop()
            model = await loop.run_in_executor(
                None,
                lambda: WhisperModel(model_size, device=device, compute_type=compute_type),
            )

            self._model = model
            self._model_size = model_size
            self._device = device
            self._compute_type = compute_type

            logger.info(
                "STT: local whisper model loaded (size=%s, device=%s, compute=%s)",
                model_size,
                device,
                compute_type,
            )
            return model

    @property
    def is_loaded(self) -> bool:
        return self._model is not None

    @property
    def current_config(self) -> dict[str, str]:
        return {
            "model_size": self._model_size,
            "device": self._device,
            "compute_type": self._compute_type,
        }


_whisper_manager = _LocalWhisperManager()


def is_local_available() -> bool:
    """Check if faster-whisper is installed and importable."""
    try:
        import faster_whisper  # noqa: F401

        return True
    except ImportError:
        return False


def local_stt_unavailable_detail(voice_config: VoiceConfig) -> str | None:
    """Return install hint when local STT is selected but faster-whisper is missing."""
    if voice_config.stt_provider.lower() != "local":
        return None
    if is_local_available():
        return None
    return f"Local STT is not installed. Install with: {LOCAL_STT_INSTALL_HINT}"


def get_local_status() -> dict[str, object]:
    """Get local STT status for the /stt/status API."""
    available = is_local_available()
    return {
        "available": available,
        "model_loaded": _whisper_manager.is_loaded,
        "config": (_whisper_manager.current_config if _whisper_manager.is_loaded else None),
    }


async def _transcribe_local(
    audio_path: Path | None,
    config: VoiceConfig,
    audio_bytes: bytes | None = None,
) -> STTResult:
    """Local transcription via faster-whisper (free, no API key, privacy-first)."""
    model = await _whisper_manager.get_model(config)
    loop = asyncio.get_running_loop()

    language = config.stt_language or None

    def _run_transcription() -> tuple[list[Segment], TranscriptionInfo]:
        import io

        audio_input = io.BytesIO(audio_bytes) if audio_bytes is not None else str(audio_path)
        segments, info = model.transcribe(
            audio_input,
            beam_size=5,
            language=language,
            vad_filter=True,
        )
        return list(segments), info

    segments, info = await loop.run_in_executor(None, _run_transcription)

    text = " ".join(seg.text.strip() for seg in segments if seg.text.strip())
    if not text:
        raise ValueError("Empty transcript returned from local whisper")

    logger.info(
        "STT: local transcription ok (model=%s, chars=%d, lang=%s)",
        config.stt_local_model,
        len(text),
        info.language,
    )
    _whisper_manager._schedule_unload()
    return STTResult(
        text=text,
        language=info.language,
        duration=info.duration,
    )
