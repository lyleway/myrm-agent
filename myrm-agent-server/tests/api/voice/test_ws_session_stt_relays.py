"""Voice session speech-to-text: the Deepgram relays and the control messages they honour."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import create_autospec, patch

import pytest
from fastapi import WebSocketDisconnect
from websockets.exceptions import ConnectionClosedOK

from app.api.voice import ws_session
from app.api.voice.agent_bridge import VoiceAgentBridge
from app.channels.voice.stt import deepgram_stream_url
from tests.api.stt.stream_testkit import (
    DeepgramConnect,
    FakeDeepgram,
    audio_frame,
    control_messages,
    deepgram_results,
    sent_json,
)
from tests.api.voice.session_testkit import socket_of, voice_session

_CLOSE_STREAM = {"type": "CloseStream"}


def _drain(queue: asyncio.Queue[str | None]) -> list[str | None]:
    items: list[str | None] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


class TestAudioUpstream:
    async def test_chunks_reach_deepgram_and_the_stream_is_closed_when_the_browser_disconnects(self) -> None:
        session = voice_session([audio_frame(b"one"), audio_frame(b"two")])
        deepgram = FakeDeepgram()

        await session._relay_client_audio(deepgram)

        assert deepgram.sent[:2] == [b"one", b"two"]
        assert control_messages(deepgram) == [_CLOSE_STREAM]

    async def test_control_messages_between_audio_chunks_are_dispatched(self) -> None:
        session = voice_session([{"type": "websocket.receive", "text": json.dumps({"type": "tts", "text": " read this "})}])

        await session._relay_client_audio(FakeDeepgram())

        assert session._tts_queue.get_nowait() == "read this"

    async def test_a_silent_browser_is_dropped_after_the_idle_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ws_session, "_SESSION_IDLE_TIMEOUT", 0.01)
        session = voice_session(hang=True)
        deepgram = FakeDeepgram()

        await session._relay_client_audio(deepgram)

        assert control_messages(deepgram) == [_CLOSE_STREAM]

    async def test_a_browser_that_drops_mid_stream_marks_the_session_closed(self) -> None:
        session = voice_session([WebSocketDisconnect()])
        deepgram = FakeDeepgram()

        await session._relay_client_audio(deepgram)

        assert session._closed
        assert control_messages(deepgram) == [_CLOSE_STREAM]

    @pytest.mark.parametrize(("excess", "forwarded"), [(0, True), (1, False)], ids=["at the limit", "over the limit"])
    async def test_a_chunk_over_the_size_limit_is_dropped_without_ending_the_stream(self, excess: int, forwarded: bool) -> None:
        chunk = b"\x01" * (ws_session._MAX_AUDIO_CHUNK + excess)
        session = voice_session([audio_frame(chunk), audio_frame(b"next")])
        deepgram = FakeDeepgram()

        await session._relay_client_audio(deepgram)

        assert deepgram.sent[:-1] == ([chunk, b"next"] if forwarded else [b"next"])

    async def test_a_provider_connection_that_is_already_gone_does_not_break_the_close(self) -> None:
        session = voice_session()
        deepgram = FakeDeepgram(send_error=ConnectionClosedOK(None, None))

        await session._relay_client_audio(deepgram)

        assert deepgram.sent == []
        assert not session._closed


class TestProviderConnection:
    async def test_a_missing_websockets_package_is_reported_to_the_browser(self) -> None:
        session = voice_session()

        with patch("websockets.connect", side_effect=ImportError("websockets")):
            await session._run_streaming_stt()

        assert sent_json(socket_of(session)) == [{"type": "error", "message": "STT provider unavailable"}]

    async def test_a_failed_provider_connection_is_reported_to_the_browser(self) -> None:
        session = voice_session()

        with patch("websockets.connect", side_effect=ConnectionRefusedError("refused")):
            await session._run_streaming_stt()

        assert sent_json(socket_of(session)) == [{"type": "error", "message": "STT connection failed"}]

    async def test_the_session_connects_with_the_api_key_to_the_shared_stream_url(self) -> None:
        session = voice_session()
        config = session._voice_config
        deepgram = FakeDeepgram()

        with patch("websockets.connect", return_value=DeepgramConnect(deepgram)) as connect:
            await session._run_streaming_stt()

        assert connect.call_args.args == (deepgram_stream_url(config.stt_model, config.stt_language, []),)
        assert connect.call_args.kwargs == {
            "additional_headers": {"Authorization": "Token key"},
            "ping_interval": 20,
            "close_timeout": 5,
        }
        assert control_messages(deepgram) == [_CLOSE_STREAM]

    async def test_the_session_ends_when_the_browser_stream_ends_even_if_deepgram_stays_silent(self) -> None:
        session = voice_session()
        deepgram = FakeDeepgram(hang=True)

        with patch("websockets.connect", return_value=DeepgramConnect(deepgram)):
            await asyncio.wait_for(session._run_streaming_stt(), timeout=5)

        assert control_messages(deepgram) == [_CLOSE_STREAM]

    async def test_the_session_ends_when_deepgram_hangs_up_even_if_the_browser_stays_connected(self) -> None:
        session = voice_session(hang=True)
        deepgram = FakeDeepgram(deepgram_results("hello", is_final=True))

        with patch("websockets.connect", return_value=DeepgramConnect(deepgram)):
            await asyncio.wait_for(session._run_streaming_stt(), timeout=5)

        assert sent_json(socket_of(session)) == [{"type": "stt_final", "text": "hello"}]
        assert control_messages(deepgram) == [_CLOSE_STREAM]


class TestControlMessages:
    async def test_a_close_message_marks_the_session_closed(self) -> None:
        session = voice_session()

        session._handle_text_message(json.dumps({"type": "close"}))

        assert session._closed

    @pytest.mark.parametrize(
        ("text", "queued"), [(" read this ", ["read this"]), ("   ", []), ("", [])], ids=["padded", "blank", "empty"]
    )
    async def test_text_to_speak_is_queued_trimmed_and_blank_text_is_ignored(self, text: str, queued: list[str]) -> None:
        session = voice_session()

        session._handle_text_message(json.dumps({"type": "tts", "text": text}))

        assert _drain(session._tts_queue) == queued

    @pytest.mark.parametrize(
        "message", ["not json", json.dumps({"type": "volume"}), "{}"], ids=["garbage", "unknown", "typeless"]
    )
    async def test_unparsable_and_unknown_messages_change_nothing(self, message: str) -> None:
        session = voice_session()

        session._handle_text_message(message)

        assert not session._closed
        assert not session._tts_cancel_event.is_set()
        assert session._tts_queue.empty()

    async def test_cancelling_speech_also_stops_the_agent_bridge_audio(self) -> None:
        bridge = create_autospec(VoiceAgentBridge, instance=True)
        session = voice_session(bridge=bridge)

        session._handle_text_message(json.dumps({"type": "tts_cancel"}))

        assert session._tts_cancel_event.is_set()
        bridge.cancel_tts.assert_called_once_with()

    async def test_cancelling_speech_without_a_bridge_only_raises_the_cancel_flag(self) -> None:
        session = voice_session()

        session._handle_text_message(json.dumps({"type": "tts_cancel"}))

        assert session._tts_cancel_event.is_set()


class TestTranscriptsDownstream:
    async def test_only_non_empty_results_are_relayed_with_their_interim_or_final_kind(self) -> None:
        session = voice_session()
        deepgram = FakeDeepgram(
            b"binary frame",
            "not json",
            json.dumps({"type": "Metadata"}),
            json.dumps({"type": "Results", "channel": {"alternatives": []}}),
            deepgram_results("   "),
            deepgram_results("hel", is_final=False),
            deepgram_results(" hello ", is_final=True),
        )

        await session._relay_deepgram_transcripts(deepgram)

        assert sent_json(socket_of(session)) == [
            {"type": "stt_interim", "text": "hel"},
            {"type": "stt_final", "text": "hello"},
        ]

    async def test_a_closed_session_stops_relaying(self) -> None:
        session = voice_session()
        session._closed = True

        await session._relay_deepgram_transcripts(FakeDeepgram(deepgram_results("late", is_final=True)))

        assert sent_json(socket_of(session)) == []

    async def test_a_closed_session_starts_no_agent_turn(self) -> None:
        bridge = create_autospec(VoiceAgentBridge, instance=True)
        session = voice_session(bridge=bridge)
        session._closed = True

        await session._relay_deepgram_transcripts(FakeDeepgram(deepgram_results("late", is_final=True)))
        await asyncio.sleep(0)

        bridge.handle_stt_final.assert_not_called()

    async def test_a_final_transcript_goes_to_the_agent_bridge_and_interim_text_stays_with_the_browser(self) -> None:
        bridge = create_autospec(VoiceAgentBridge, instance=True)
        session = voice_session(bridge=bridge)

        await session._relay_deepgram_transcripts(
            FakeDeepgram(deepgram_results("book", is_final=False), deepgram_results("book a table", is_final=True))
        )
        await asyncio.sleep(0)

        bridge.handle_stt_final.assert_awaited_once_with("book a table")
        assert sent_json(socket_of(session)) == [{"type": "stt_interim", "text": "book"}]

    @pytest.mark.parametrize(
        ("closed", "reported"),
        [(False, [{"type": "error", "message": "Agent processing failed"}]), (True, [])],
        ids=["open session", "closed session"],
    )
    async def test_a_failed_agent_turn_is_logged_and_reported_only_to_an_open_session(
        self, closed: bool, reported: list[dict[str, object]], caplog: pytest.LogCaptureFixture
    ) -> None:
        async def failing_turn() -> None:
            raise RuntimeError("agent crashed")

        session = voice_session()
        session._closed = closed
        turn = asyncio.create_task(failing_turn())
        await asyncio.wait({turn})

        with caplog.at_level(logging.ERROR):
            session._on_bridge_task_done(turn)
            await asyncio.sleep(0)

        assert "Agent bridge task failed" in caplog.text
        assert sent_json(socket_of(session)) == reported

    async def test_a_relay_failure_is_logged_while_the_session_is_open(self, caplog: pytest.LogCaptureFixture) -> None:
        session = voice_session()

        with caplog.at_level(logging.WARNING):
            await session._relay_deepgram_transcripts(FakeDeepgram(then=RuntimeError("socket reset")))

        assert "Deepgram transcript relay error" in caplog.text

    async def test_a_relay_failure_after_the_session_closed_is_not_logged(self, caplog: pytest.LogCaptureFixture) -> None:
        session = voice_session()
        session._closed = True

        with caplog.at_level(logging.WARNING):
            await session._relay_deepgram_transcripts(FakeDeepgram(then=RuntimeError("socket reset")))

        assert "Deepgram transcript relay error" not in caplog.text
