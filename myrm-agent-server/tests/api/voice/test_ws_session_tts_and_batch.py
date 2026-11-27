"""Voice session text-to-speech streaming and the batch speech-to-text loop for providers without live streaming."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Callable, Sequence
from unittest.mock import AsyncMock, create_autospec, patch

import pytest
from fastapi import WebSocketDisconnect

from app.api.voice import ws_session
from app.api.voice.agent_bridge import VoiceAgentBridge
from app.api.voice.ws_session import _VoiceSession
from app.channels.types import VoiceConfig
from tests.api.stt.stream_testkit import FakeTranscriber, Frame, audio_frame, sent_json
from tests.api.voice.session_testkit import socket_of, voice_session

_INFO = {"type": "info", "message": "streaming_unavailable", "fallback": "batch"}
_END_UTTERANCE: dict[str, object] = {"type": "websocket.receive", "text": json.dumps({"type": "end_utterance"})}
_UTTERANCE_AUDIO = b"\x01" * 2048
_MIN_UTTERANCE_BYTES = 1024
_MAX_UTTERANCE_BYTES = 25 * 1024 * 1024


def _session(frames: Sequence[Frame] = (), *, bridge: VoiceAgentBridge | None = None, hang: bool = False) -> _VoiceSession:
    """A session for a provider without live streaming, so it runs the batch loop."""
    return voice_session(frames, provider="openai", bridge=bridge, hang=hang)


def _synthesizer(
    *chunks: bytes, error: Exception | None = None, after_first: Callable[[], None] | None = None
) -> Callable[[str, VoiceConfig], AsyncIterator[bytes]]:
    """A `synthesize_stream` yielding `chunks`; runs `after_first` once the first chunk was taken, then raises `error`."""

    async def synthesize(text: str, config: VoiceConfig) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(chunks):
            yield chunk
            if index == 0 and after_first is not None:
                after_first()
        if error is not None:
            raise error

    return synthesize


def _spoken(session: _VoiceSession) -> list[bytes]:
    return [call.args[0] for call in socket_of(session).send_bytes.await_args_list]


class TestSpeechStreaming:
    async def test_synthesised_audio_is_streamed_between_start_and_end_markers(self) -> None:
        session = _session()

        with patch("app.channels.voice.tts.synthesize_stream", _synthesizer(b"a", b"b")):
            await session._stream_tts("hello")

        assert _spoken(session) == [b"a", b"b"]
        assert sent_json(socket_of(session)) == [{"type": "tts_start"}, {"type": "tts_end"}]

    async def test_cancelling_speech_mid_stream_stops_the_audio_but_still_ends_the_turn(self) -> None:
        session = _session()
        synthesizer = _synthesizer(b"a", b"b", after_first=session._tts_cancel_event.set)

        with patch("app.channels.voice.tts.synthesize_stream", synthesizer):
            await session._stream_tts("hello")

        assert _spoken(session) == [b"a"]
        assert sent_json(socket_of(session))[-1] == {"type": "tts_end"}

    async def test_speech_after_a_cancel_starts_with_a_clean_flag(self) -> None:
        session = _session()
        session._tts_cancel_event.set()

        with patch("app.channels.voice.tts.synthesize_stream", _synthesizer(b"a")):
            await session._stream_tts("hello")

        assert _spoken(session) == [b"a"]

    @pytest.mark.parametrize("failure", [WebSocketDisconnect(), RuntimeError("closed")], ids=["disconnected", "closed"])
    async def test_a_browser_that_vanishes_while_audio_is_sent_closes_the_session(self, failure: Exception) -> None:
        session = _session()
        socket_of(session).send_bytes.side_effect = failure

        with patch("app.channels.voice.tts.synthesize_stream", _synthesizer(b"a", b"b")):
            await session._stream_tts("hello")

        assert session._closed
        assert sent_json(socket_of(session)) == [{"type": "tts_start"}]

    async def test_a_synthesis_failure_is_logged_and_the_turn_still_ends(self, caplog: pytest.LogCaptureFixture) -> None:
        session = _session()
        synthesizer = _synthesizer(b"a", error=RuntimeError("provider down"))

        with patch("app.channels.voice.tts.synthesize_stream", synthesizer), caplog.at_level(logging.ERROR):
            await session._stream_tts("hello")

        assert "TTS streaming failed in voice session" in caplog.text
        assert _spoken(session) == [b"a"]
        assert sent_json(socket_of(session))[-1] == {"type": "tts_end"}

    async def test_the_consumer_speaks_queued_text_in_order_until_the_end_marker(self) -> None:
        session = _session()
        for item in ("first", "second", None):
            session._tts_queue.put_nowait(item)

        with patch.object(_VoiceSession, "_stream_tts", new_callable=AsyncMock) as stream:
            await session._tts_consumer()

        assert [call.args for call in stream.await_args_list] == [("first",), ("second",)]


class TestBatchSpeechToText:
    async def test_a_silent_browser_closes_the_session_after_the_idle_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ws_session, "_SESSION_IDLE_TIMEOUT", 0.01)
        session = _session(hang=True)

        await session._run_batch_stt()

        assert session._closed
        assert sent_json(socket_of(session)) == [_INFO]

    async def test_a_disconnect_frame_closes_the_session(self) -> None:
        session = _session()

        await session._run_batch_stt()

        assert session._closed

    async def test_a_browser_that_drops_mid_receive_ends_the_loop_quietly(self) -> None:
        session = _session([WebSocketDisconnect()])

        await session._run_batch_stt()

        assert sent_json(socket_of(session)) == [_INFO]

    async def test_an_utterance_without_audio_is_skipped_and_the_next_one_is_transcribed(self) -> None:
        session = _session([_END_UTTERANCE, audio_frame(_UTTERANCE_AUDIO), _END_UTTERANCE])

        with patch.object(_VoiceSession, "_batch_transcribe", new_callable=AsyncMock, return_value="turn it on") as transcribe:
            await session._run_batch_stt()

        transcribe.assert_awaited_once_with(_UTTERANCE_AUDIO)
        assert sent_json(socket_of(session)) == [_INFO, {"type": "stt_final", "text": "turn it on"}]

    @pytest.mark.parametrize(
        ("size", "transcribed"),
        [(_MIN_UTTERANCE_BYTES - 1, False), (_MIN_UTTERANCE_BYTES, True)],
        ids=["under one kilobyte", "one kilobyte"],
    )
    async def test_only_utterances_of_at_least_one_kilobyte_are_transcribed(self, size: int, transcribed: bool) -> None:
        session = _session([audio_frame(b"\x01" * size), _END_UTTERANCE])

        with patch.object(_VoiceSession, "_batch_transcribe", new_callable=AsyncMock, return_value="hi") as transcribe:
            await session._run_batch_stt()

        assert transcribe.await_count == (1 if transcribed else 0)

    async def test_audio_beyond_the_size_limit_is_rejected_and_nothing_is_transcribed(self) -> None:
        session = _session([audio_frame(b"\x00" * (_MAX_UTTERANCE_BYTES + 1)), _END_UTTERANCE])

        with patch.object(_VoiceSession, "_batch_transcribe", new_callable=AsyncMock) as transcribe:
            await session._run_batch_stt()

        transcribe.assert_not_awaited()
        assert sent_json(socket_of(session)) == [_INFO, {"type": "error", "message": "Audio too large"}]

    async def test_a_transcribed_utterance_goes_to_the_agent_bridge_instead_of_the_browser(self) -> None:
        bridge = create_autospec(VoiceAgentBridge, instance=True)
        session = _session([audio_frame(_UTTERANCE_AUDIO), _END_UTTERANCE], bridge=bridge)

        with patch.object(_VoiceSession, "_batch_transcribe", new_callable=AsyncMock, return_value="turn it on"):
            await session._run_batch_stt()
        await asyncio.sleep(0)

        bridge.handle_stt_final.assert_awaited_once_with("turn it on")
        assert sent_json(socket_of(session)) == [_INFO]


class TestBatchTranscription:
    async def test_the_transcript_is_returned_and_the_temp_file_removed(self) -> None:
        transcriber = FakeTranscriber("hello")

        with patch("app.channels.voice.stt.transcribe", transcriber):
            text = await _session()._batch_transcribe(_UTTERANCE_AUDIO)

        assert text == "hello"
        assert transcriber.audio == _UTTERANCE_AUDIO
        assert transcriber.path is not None and transcriber.path.suffix == ".webm"
        assert transcriber.temp_file_was_removed()

    @pytest.mark.parametrize(
        "transcriber",
        [FakeTranscriber(text=None), FakeTranscriber(text="")],
        ids=["no result", "empty text"],
    )
    async def test_a_transcript_without_text_comes_back_as_none_and_the_temp_file_is_removed(
        self, transcriber: FakeTranscriber
    ) -> None:
        with patch("app.channels.voice.stt.transcribe", transcriber):
            text = await _session()._batch_transcribe(_UTTERANCE_AUDIO)

        assert text is None
        assert transcriber.temp_file_was_removed()

    async def test_a_provider_failure_is_logged_and_the_temp_file_is_still_removed(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        transcriber = FakeTranscriber(error=RuntimeError("provider down"))

        with patch("app.channels.voice.stt.transcribe", transcriber), caplog.at_level(logging.ERROR):
            text = await _session()._batch_transcribe(_UTTERANCE_AUDIO)

        assert text is None
        assert "Batch STT failed in voice session" in caplog.text
        assert transcriber.temp_file_was_removed()
