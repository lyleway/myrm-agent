"""Local faster-whisper: the transcribe() local path, the model manager and the availability probes."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.channels.types import VoiceConfig
from app.channels.voice.stt import get_local_status, is_local_available, transcribe
from app.channels.voice.stt.local import _LocalWhisperManager
from tests.channels.voice.stt.stt_testkit import FakeSegment, FakeTranscriptionInfo, audio_file, mock_whisper_model, stt_config


class TestTranscribeLocal:
    @pytest.mark.asyncio
    async def test_local_transcription_success(self) -> None:
        path = audio_file()
        mock_model = mock_whisper_model()

        with patch.object(_LocalWhisperManager, "get_model", new_callable=AsyncMock, return_value=mock_model):
            try:
                result = await transcribe(path, stt_config("local"))
            finally:
                path.unlink(missing_ok=True)

        assert result is not None
        assert result.text == "Hello local world"
        assert result.language == "en"
        assert result.duration == 2.1
        mock_model.transcribe.assert_called_once()
        call_kwargs = mock_model.transcribe.call_args
        assert call_kwargs.kwargs.get("vad_filter") is True
        assert call_kwargs.kwargs.get("beam_size") == 5

    @pytest.mark.asyncio
    async def test_local_transcription_empty_text_raises(self) -> None:
        path = audio_file()
        mock_model = MagicMock()
        mock_model.transcribe.return_value = (
            [FakeSegment(text="  "), FakeSegment(text="")],
            FakeTranscriptionInfo(language="en", duration=1.0),
        )

        with patch.object(_LocalWhisperManager, "get_model", new_callable=AsyncMock, return_value=mock_model):
            try:
                result = await transcribe(path, stt_config("local"))
            finally:
                path.unlink(missing_ok=True)

        # Empty text triggers fallback → no cloud key → returns None
        assert result is None

    @pytest.mark.asyncio
    async def test_local_passes_language_config(self) -> None:
        path = audio_file()
        mock_model = mock_whisper_model()
        config = VoiceConfig(
            stt_enabled=True,
            stt_provider="local",
            stt_language="zh",
        )

        with patch.object(_LocalWhisperManager, "get_model", new_callable=AsyncMock, return_value=mock_model):
            try:
                await transcribe(path, config)
            finally:
                path.unlink(missing_ok=True)

        call_kwargs = mock_model.transcribe.call_args
        assert call_kwargs.kwargs.get("language") == "zh"


class TestLocalWhisperManager:
    def test_resolve_device_auto_no_torch(self) -> None:
        mgr = _LocalWhisperManager()
        with patch.dict("sys.modules", {"torch": None}):
            assert mgr._resolve_device("auto") == "cpu"

    def test_resolve_device_explicit(self) -> None:
        mgr = _LocalWhisperManager()
        assert mgr._resolve_device("cuda") == "cuda"
        assert mgr._resolve_device("cpu") == "cpu"

    def test_resolve_compute_type_auto_cuda(self) -> None:
        mgr = _LocalWhisperManager()
        assert mgr._resolve_compute_type("auto", "cuda") == "float16"

    def test_resolve_compute_type_auto_cpu(self) -> None:
        mgr = _LocalWhisperManager()
        assert mgr._resolve_compute_type("auto", "cpu") == "int8"

    def test_resolve_compute_type_explicit(self) -> None:
        mgr = _LocalWhisperManager()
        assert mgr._resolve_compute_type("float32", "cpu") == "float32"

    def test_is_loaded_default_false(self) -> None:
        mgr = _LocalWhisperManager()
        assert mgr.is_loaded is False

    def test_current_config_empty_before_load(self) -> None:
        mgr = _LocalWhisperManager()
        config = mgr.current_config
        assert config["model_size"] == ""
        assert config["device"] == ""

    @pytest.mark.asyncio
    async def test_get_model_loads_and_caches(self) -> None:
        mgr = _LocalWhisperManager()
        mock_model = MagicMock()

        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=lambda *a, **kw: mock_model)}):
            config = stt_config("local", local_model="tiny", local_device="cpu", local_compute_type="int8")
            model = await mgr.get_model(config)
            assert model is mock_model
            assert mgr.is_loaded is True
            assert mgr.current_config["model_size"] == "tiny"
            assert mgr.current_config["device"] == "cpu"
            assert mgr.current_config["compute_type"] == "int8"


class TestWhisperManagerCacheHit:
    @pytest.mark.asyncio
    async def test_cached_model_reused_on_same_config(self) -> None:
        mgr = _LocalWhisperManager()
        mock_model = MagicMock()

        with patch.dict("sys.modules", {"faster_whisper": SimpleNamespace(WhisperModel=lambda *a, **kw: mock_model)}):
            config = stt_config("local", local_model="tiny", local_device="cpu", local_compute_type="int8")
            model1 = await mgr.get_model(config)
            model2 = await mgr.get_model(config)
            assert model1 is model2
            assert model1 is mock_model


class TestWhisperManagerCudaDetection:
    def test_resolve_device_auto_with_cuda(self) -> None:
        mgr = _LocalWhisperManager()
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = True
        with patch.dict("sys.modules", {"torch": mock_torch}):
            assert mgr._resolve_device("auto") == "cuda"

    def test_resolve_device_auto_without_cuda(self) -> None:
        mgr = _LocalWhisperManager()
        mock_torch = MagicMock()
        mock_torch.cuda.is_available.return_value = False
        with patch.dict("sys.modules", {"torch": mock_torch}):
            assert mgr._resolve_device("auto") == "cpu"


class TestLocalAvailability:
    def test_is_local_available_when_installed(self) -> None:
        with patch.dict("sys.modules", {"faster_whisper": MagicMock()}):
            assert is_local_available() is True

    def test_is_local_available_when_not_installed(self) -> None:
        with patch.dict("sys.modules", {"faster_whisper": None}):
            with patch(
                "builtins.__import__",
                side_effect=lambda name, *a, **kw: (
                    (_ for _ in ()).throw(ImportError) if name == "faster_whisper" else __builtins__.__import__(name, *a, **kw)
                ),
            ):
                assert is_local_available() is False

    def test_get_local_status_not_loaded(self) -> None:
        with (
            patch("app.channels.voice.stt.local.is_local_available", return_value=False),
            patch("app.channels.voice.stt.local._whisper_manager") as mock_mgr,
        ):
            mock_mgr.is_loaded = False
            status = get_local_status()
            assert status["available"] is False
            assert status["model_loaded"] is False
            assert status["config"] is None
