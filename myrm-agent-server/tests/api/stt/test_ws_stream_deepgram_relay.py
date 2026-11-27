"""/ws/stt/stream Deepgram proxy: the provider connection, the audio going up and the transcripts coming back."""

from __future__ import annotations

import json
import logging
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocketDisconnect
from websockets.exceptions import ConnectionClosedOK

from app.api.stt import ws_stream
from app.channels.types import VoiceConfig
from app.channels.voice.stt import deepgram_stream_url
from tests.api.stt.stream_testkit import (
    DeepgramConnect,
    FakeDeepgram,
    audio_frame,
    browser_socket,
    control_messages,
    deepgram_results,
    sent_json,
)

_CLOSE_STREAM = {"type": "CloseStream"}
_CONFIG = VoiceConfig(stt_enabled=True, stt_provider="deepgram", stt_api_key="secret", stt_model="nova-2", stt_language="de")


def _text(**message: object) -> dict[str, object]:
    return {"type": "websocket.receive", "text": json.dumps(message)}


class TestProviderConnection:
    async def test_the_connection_is_opened_with_the_stored_key_and_settings(self) -> None:
        ws = browser_socket(hang=True)
        deepgram = FakeDeepgram()

        with patch("websockets.connect", return_value=DeepgramConnect(deepgram)) as connect:
            await ws_stream._stream_via_deepgram(ws, _CONFIG, ["alpha"])

        assert connect.call_args.args == (deepgram_stream_url("nova-2", "de", ["alpha"]),)
        assert connect.call_args.kwargs == {
            "additional_headers": {"Authorization": "Token secret"},
            "ping_interval": 20,
            "close_timeout": 5,
        }

    async def test_transcripts_reach_the_browser_and_the_stream_is_closed_when_deepgram_finishes(self) -> None:
        ws = browser_socket(hang=True)
        deepgram = FakeDeepgram(deepgram_results("hello", is_final=True))

        with patch("websockets.connect", return_value=DeepgramConnect(deepgram)):
            await ws_stream._stream_via_deepgram(ws, _CONFIG, [])

        assert sent_json(ws) == [{"type": "transcript", "text": "hello", "is_final": True}]
        assert control_messages(deepgram)[-1] == _CLOSE_STREAM

    async def test_the_provider_stream_is_dropped_when_the_browser_leaves_first(self) -> None:
        ws = browser_socket()
        deepgram = FakeDeepgram(hang=True)

        with patch("websockets.connect", return_value=DeepgramConnect(deepgram)):
            await ws_stream._stream_via_deepgram(ws, _CONFIG, [])

        assert control_messages(deepgram)[-1] == _CLOSE_STREAM

    async def test_a_relay_that_fails_unexpectedly_is_logged_instead_of_raised(self, caplog: pytest.LogCaptureFixture) -> None:
        ws = browser_socket(hang=True)
        ws.send_text.side_effect = ValueError("boom")
        deepgram = FakeDeepgram(deepgram_results("hi", is_final=True))

        with patch("websockets.connect", return_value=DeepgramConnect(deepgram)), caplog.at_level(logging.WARNING):
            await ws_stream._stream_via_deepgram(ws, _CONFIG, [])

        assert "STT stream task error: boom" in caplog.text

    async def test_a_failed_provider_connection_is_reported_and_ends_the_session(self) -> None:
        ws = browser_socket()

        with patch("websockets.connect", side_effect=ConnectionRefusedError("refused")):
            await ws_stream._stream_via_deepgram(ws, _CONFIG, [])

        assert sent_json(ws) == [{"type": "error", "message": "STT provider connection failed"}]
        ws.close.assert_awaited_once_with(code=1011, reason="Provider error")

    async def test_a_missing_websockets_package_falls_back_to_batch_transcription(self) -> None:
        ws = browser_socket()

        with (
            patch("websockets.connect", side_effect=ImportError("websockets")),
            patch.object(ws_stream, "_batch_fallback", new_callable=AsyncMock) as batch,
        ):
            await ws_stream._stream_via_deepgram(ws, _CONFIG, [])

        batch.assert_awaited_once_with(ws, _CONFIG)


class TestAudioUpstream:
    async def test_chunks_are_forwarded_in_order_and_the_stream_closed_when_the_browser_disconnects(self) -> None:
        ws = browser_socket([audio_frame(b"chunk-1"), audio_frame(b"chunk-2")])
        deepgram = FakeDeepgram()

        await ws_stream._relay_client_audio(ws, deepgram)

        assert deepgram.sent[:2] == [b"chunk-1", b"chunk-2"]
        assert control_messages(deepgram) == [_CLOSE_STREAM]

    async def test_oversized_empty_and_unrelated_frames_never_reach_deepgram(self) -> None:
        ws = browser_socket(
            [
                audio_frame(b"x" * (ws_stream._MAX_AUDIO_CHUNK + 1)),
                audio_frame(b""),
                {"type": "websocket.receive", "text": "not json"},
                _text(action="ping"),
                audio_frame(b"kept"),
            ]
        )
        deepgram = FakeDeepgram()

        await ws_stream._relay_client_audio(ws, deepgram)

        assert [message for message in deepgram.sent if isinstance(message, bytes)] == [b"kept"]

    async def test_a_close_request_ends_the_stream_without_reading_later_frames(self) -> None:
        ws = browser_socket([_text(action="close"), audio_frame(b"late")])
        deepgram = FakeDeepgram()

        await ws_stream._relay_client_audio(ws, deepgram)

        assert b"late" not in deepgram.sent
        assert _CLOSE_STREAM in control_messages(deepgram)

    async def test_a_silent_browser_is_dropped_after_the_idle_timeout(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ws_stream, "_SESSION_IDLE_TIMEOUT", 0.01)
        ws = browser_socket(hang=True)
        deepgram = FakeDeepgram()

        await ws_stream._relay_client_audio(ws, deepgram)

        assert control_messages(deepgram) == [_CLOSE_STREAM]

    async def test_a_browser_that_drops_mid_stream_still_closes_the_provider_stream(self) -> None:
        ws = browser_socket([audio_frame(b"chunk"), WebSocketDisconnect()])
        deepgram = FakeDeepgram()

        await ws_stream._relay_client_audio(ws, deepgram)

        assert deepgram.sent[0] == b"chunk"
        assert control_messages(deepgram) == [_CLOSE_STREAM]

    async def test_a_provider_connection_that_is_already_gone_does_not_break_the_close(self) -> None:
        ws = browser_socket()
        deepgram = FakeDeepgram(send_error=ConnectionClosedOK(None, None))

        await ws_stream._relay_client_audio(ws, deepgram)

        assert deepgram.sent == []


class TestTranscriptsDownstream:
    async def test_only_non_empty_results_are_relayed_and_is_final_defaults_to_false(self) -> None:
        ws = browser_socket()
        deepgram = FakeDeepgram(
            b"binary frame",
            "not json",
            json.dumps({"type": "Metadata"}),
            json.dumps({"type": "Results", "channel": {"alternatives": []}}),
            deepgram_results("   "),
            deepgram_results("hel", is_final=False),
            deepgram_results(" hello ", is_final=True),
            deepgram_results("no flag"),
        )

        await ws_stream._relay_deepgram_transcripts(ws, deepgram)

        assert sent_json(ws) == [
            {"type": "transcript", "text": "hel", "is_final": False},
            {"type": "transcript", "text": "hello", "is_final": True},
            {"type": "transcript", "text": "no flag", "is_final": False},
        ]

    @pytest.mark.parametrize(
        "ending", [ConnectionClosedOK(None, None), WebSocketDisconnect()], ids=["provider closed", "browser gone"]
    )
    async def test_the_relay_ends_quietly_when_either_side_goes_away(self, ending: Exception) -> None:
        ws = browser_socket()
        deepgram = FakeDeepgram(deepgram_results("last", is_final=True), then=ending)

        await ws_stream._relay_deepgram_transcripts(ws, deepgram)

        assert [message["text"] for message in sent_json(ws)] == ["last"]
