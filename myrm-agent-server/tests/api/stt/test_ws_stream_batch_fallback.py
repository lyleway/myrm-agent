"""/ws/stt/stream for providers without live streaming: the audio is collected, then transcribed in one request."""

from __future__ import annotations

import json
from collections.abc import Sequence
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocketDisconnect

from app.api.stt import ws_stream
from app.channels.types import VoiceConfig
from tests.api.stt.stream_testkit import FakeTranscriber, Frame, audio_frame, browser_socket, sent_json

_CONFIG = VoiceConfig(stt_enabled=True, stt_provider="openai", stt_api_key="key")
_INFO = {"type": "info", "message": "streaming_unavailable", "fallback": "batch"}
_CLOSE = {"type": "websocket.receive", "text": json.dumps({"action": "close"})}
_MIN_AUDIO_BYTES = 1024


async def _run(
    frames: Sequence[Frame], transcriber: FakeTranscriber | None = None, *, hang: bool = False
) -> tuple[AsyncMock, FakeTranscriber]:
    ws = browser_socket(frames, hang=hang)
    transcriber = transcriber or FakeTranscriber()
    with patch("app.channels.voice.stt.transcribe", transcriber):
        await ws_stream._batch_fallback(ws, _CONFIG)
    return ws, transcriber


class TestBatchTranscription:
    async def test_the_collected_audio_is_transcribed_once_and_audio_after_the_close_action_is_left_out(self) -> None:
        first, second, late = b"\x01" * 600, b"\x02" * 600, b"\x03" * 600

        ws, transcriber = await _run([audio_frame(first), audio_frame(second), _CLOSE, audio_frame(late)])

        assert sent_json(ws) == [_INFO, {"type": "transcript", "text": "hello", "is_final": True}]
        assert transcriber.audio == first + second
        assert transcriber.path is not None and transcriber.path.suffix == ".webm"
        assert transcriber.temp_file_was_removed()
        ws.close.assert_awaited_once_with(code=1000, reason="Done")

    async def test_a_provider_that_returns_no_text_leaves_the_browser_without_a_transcript(self) -> None:
        ws, transcriber = await _run([audio_frame(b"\x01" * _MIN_AUDIO_BYTES), _CLOSE], FakeTranscriber(text=None))

        assert sent_json(ws) == [_INFO]
        assert transcriber.temp_file_was_removed()
        ws.close.assert_awaited_once_with(code=1000, reason="Done")

    async def test_a_transcription_failure_is_reported_and_the_temp_file_is_removed(self) -> None:
        ws, transcriber = await _run(
            [audio_frame(b"\x01" * _MIN_AUDIO_BYTES), _CLOSE], FakeTranscriber(error=RuntimeError("provider down"))
        )

        assert sent_json(ws) == [_INFO, {"type": "error", "message": "Transcription failed"}]
        assert transcriber.temp_file_was_removed()
        ws.close.assert_awaited_once_with(code=1000, reason="Done")

    async def test_audio_collected_before_the_browser_drops_is_still_transcribed(self) -> None:
        ws, transcriber = await _run([audio_frame(b"\x01" * _MIN_AUDIO_BYTES), WebSocketDisconnect()])

        assert transcriber.audio == b"\x01" * _MIN_AUDIO_BYTES
        assert sent_json(ws)[-1] == {"type": "transcript", "text": "hello", "is_final": True}

    async def test_unparsable_control_frames_are_ignored(self) -> None:
        ws, transcriber = await _run([{"type": "websocket.receive", "text": "not json"}, audio_frame(b"\x01" * _MIN_AUDIO_BYTES)])

        assert transcriber.audio == b"\x01" * _MIN_AUDIO_BYTES
        assert sent_json(ws)[-1]["text"] == "hello"


class TestAudioThatIsNotTranscribed:
    @pytest.mark.parametrize(
        ("frames", "reason"),
        [
            ([], "No audio"),
            ([_CLOSE], "No audio"),
            ([audio_frame(b"\x01" * (_MIN_AUDIO_BYTES - 1)), _CLOSE], "Audio too short"),
        ],
        ids=["nothing sent", "closed straight away", "under one kilobyte"],
    )
    async def test_the_session_ends_without_a_transcription_request(self, frames: Sequence[Frame], reason: str) -> None:
        ws, transcriber = await _run(frames)

        assert sent_json(ws) == [_INFO]
        assert transcriber.path is None
        ws.close.assert_awaited_once_with(code=1000, reason=reason)

    async def test_audio_beyond_the_size_limit_is_rejected(self) -> None:
        ws, transcriber = await _run([audio_frame(b"\x00" * (25 * 1024 * 1024 + 1))])

        assert sent_json(ws) == [_INFO, {"type": "error", "message": "Audio too large"}]
        assert transcriber.path is None
        ws.close.assert_awaited_once_with(code=1000, reason="No audio")

    async def test_a_silent_browser_is_dropped_after_the_idle_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ws_stream, "_SESSION_IDLE_TIMEOUT", 0.01)

        ws, transcriber = await _run([], hang=True)

        assert transcriber.path is None
        ws.close.assert_awaited_once_with(code=1000, reason="No audio")
