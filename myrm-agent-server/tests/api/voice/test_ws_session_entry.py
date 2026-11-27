"""Voice session entry: the opening-message checks, the voice config lookup, sending to the browser and the run loop."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import WebSocketDisconnect

from app.api.voice import ws_session
from app.api.voice.ws_session import _VoiceSession
from app.channels.types import VoiceConfig
from tests.api.stt.stream_testkit import browser_socket, sent_json, stored_voice_settings
from tests.api.voice.session_testkit import socket_of, voice_config, voice_session

_CONFIG = voice_config()
_STT_DISABLED = VoiceConfig(stt_enabled=False, stt_provider="deepgram", stt_api_key="key")


async def _enter(first_message: str | Exception, *, stored: VoiceConfig | None = _CONFIG) -> tuple[AsyncMock, MagicMock]:
    """Run `ws_voice_session` on a socket whose first message is `first_message`; returns it and the session class stand-in."""
    ws = browser_socket()
    ws.receive_text.side_effect = [first_message]
    with (
        patch.object(ws_session, "verify_ws_origin", new_callable=AsyncMock, return_value=True),
        patch.object(ws_session, "_load_voice_config", new_callable=AsyncMock, return_value=stored),
        patch.object(ws_session, "_VoiceSession") as session_cls,
    ):
        session_cls.return_value.run = AsyncMock()
        await ws_session.ws_voice_session(ws)
    return ws, session_cls


class TestSessionEntry:
    async def test_a_refused_origin_never_gets_the_voice_socket_accepted(self) -> None:
        ws = browser_socket()

        with patch.object(ws_session, "verify_ws_origin", new_callable=AsyncMock, return_value=False):
            await ws_session.ws_voice_session(ws)

        ws.accept.assert_not_awaited()

    @pytest.mark.parametrize(
        ("first_message", "reason"),
        [
            ("not json", "Invalid config"),
            ("[]", "Invalid config"),
            ("null", "Invalid config"),
            (json.dumps({"type": "config", "keyterms": "Myrm"}), "Invalid config"),
            (json.dumps({"type": "config", "chat_id": 7}), "Invalid config"),
            (WebSocketDisconnect(), "Invalid config"),
            (asyncio.TimeoutError(), "Invalid config"),
            (json.dumps({"type": "tts", "text": "hi"}), "First message must be type:config"),
            (json.dumps({"type": "config", "chat_id": "../etc/passwd"}), "Invalid chat_id"),
        ],
        ids=[
            "unparsable",
            "an array",
            "null",
            "keyterms not a list",
            "chat id not a string",
            "browser gone",
            "too slow",
            "not a config",
            "unsafe chat id",
        ],
    )
    async def test_a_bad_opening_message_is_refused_and_no_session_starts(
        self, first_message: str | Exception, reason: str
    ) -> None:
        ws, session_cls = await _enter(first_message)

        ws.close.assert_awaited_once_with(code=1011, reason=reason)
        session_cls.assert_not_called()

    async def test_without_a_usable_voice_config_no_session_starts(self) -> None:
        ws, session_cls = await _enter(json.dumps({"type": "config"}), stored=None)

        session_cls.assert_not_called()
        ws.close.assert_not_awaited()

    async def test_an_audio_only_session_is_built_from_the_config_message(self) -> None:
        ws, session_cls = await _enter(json.dumps({"type": "config", "keyterms": ["Myrm"]}))

        session_cls.assert_called_once_with(ws=ws, voice_config=_CONFIG, keyterms=["Myrm"])
        session_cls.return_value.run.assert_awaited_once_with()

    async def test_an_agent_bridge_session_shares_its_send_lock_with_the_bridge(self) -> None:
        config = {"type": "config", "mode": "agent_bridge", "agent_id": "a1", "chat_id": "chat-1", "keyterms": ["Myrm"]}

        with patch("app.api.voice.agent_bridge.VoiceAgentBridge") as bridge_cls:
            ws, session_cls = await _enter(json.dumps(config))

        bridge_kwargs = dict(bridge_cls.call_args.kwargs)
        send_lock = bridge_kwargs.pop("_ws_send_lock")
        assert bridge_kwargs == {"_ws": ws, "_voice_config": _CONFIG, "_agent_id": "a1", "_chat_id": "chat-1"}
        assert isinstance(send_lock, asyncio.Lock)
        assert session_cls.call_args.kwargs == {
            "ws": ws,
            "voice_config": _CONFIG,
            "keyterms": ["Myrm"],
            "agent_bridge": bridge_cls.return_value,
            "ws_send_lock": send_lock,
        }
        session_cls.return_value.run.assert_awaited_once_with()

    async def test_the_config_the_frontend_sends_for_a_new_chat_is_accepted(self) -> None:
        config = {"type": "config", "mode": "agent_bridge", "agent_id": None, "chat_id": None, "keyterms": []}

        with patch("app.api.voice.agent_bridge.VoiceAgentBridge") as bridge_cls:
            ws, session_cls = await _enter(json.dumps(config))

        assert (bridge_cls.call_args.kwargs["_agent_id"], bridge_cls.call_args.kwargs["_chat_id"]) == (None, None)
        assert session_cls.call_args.kwargs["keyterms"] == []
        session_cls.return_value.run.assert_awaited_once_with()
        ws.close.assert_not_awaited()


class TestVoiceConfigLookup:
    async def test_strict_deployments_refuse_an_anonymous_socket(self) -> None:
        ws = browser_socket()

        with stored_voice_settings(_CONFIG, strict_auth=True):
            loaded = await ws_session._load_voice_config(ws)

        assert loaded is None
        assert sent_json(ws) == [{"type": "error", "message": "Authentication required"}]
        ws.close.assert_awaited_once_with(code=1011, reason="Unauthorized")

    @pytest.mark.parametrize("user_id", ["", "alice"], ids=["local anonymous", "signed in"])
    async def test_the_stored_config_is_returned_when_stt_is_enabled(self, user_id: str) -> None:
        ws = browser_socket(user_id=user_id)

        with stored_voice_settings(_CONFIG):
            loaded = await ws_session._load_voice_config(ws)

        assert loaded is _CONFIG
        ws.close.assert_not_awaited()

    @pytest.mark.parametrize("extracted", [None, _STT_DISABLED], ids=["no voice settings", "stt disabled"])
    async def test_a_missing_or_disabled_stt_config_ends_the_session(self, extracted: VoiceConfig | None) -> None:
        ws = browser_socket(user_id="alice")

        with stored_voice_settings(extracted):
            loaded = await ws_session._load_voice_config(ws)

        assert loaded is None
        assert sent_json(ws) == [{"type": "error", "message": "Voice not configured"}]
        ws.close.assert_awaited_once_with(code=1011, reason="Voice not configured")

    @pytest.mark.parametrize(
        ("user_id", "logged_user"), [("alice", "alice"), ("", "sandbox")], ids=["signed in", "local anonymous"]
    )
    async def test_a_failing_config_store_is_logged_and_reported_as_not_configured(
        self, user_id: str, logged_user: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        ws = browser_socket(user_id=user_id)

        with stored_voice_settings(None, load_error=RuntimeError("db down")), caplog.at_level(logging.WARNING):
            loaded = await ws_session._load_voice_config(ws)

        assert loaded is None
        assert f"Failed to load voice config for WS user {logged_user}" in caplog.text
        assert sent_json(ws) == [{"type": "error", "message": "Voice not configured"}]


class TestSendingToTheBrowser:
    async def test_a_closed_session_sends_nothing(self) -> None:
        session = voice_session()
        session._closed = True

        await session._send_json({"type": "stt_final", "text": "hi"})

        socket_of(session).send_text.assert_not_awaited()

    @pytest.mark.parametrize("failure", [WebSocketDisconnect(), RuntimeError("closed")], ids=["disconnected", "closed"])
    async def test_a_session_whose_browser_vanished_marks_itself_closed(self, failure: Exception) -> None:
        session = voice_session()
        socket_of(session).send_text.side_effect = failure

        await session._send_json({"type": "stt_final", "text": "hi"})

        assert session._closed

    @pytest.mark.parametrize("failure", [WebSocketDisconnect(), RuntimeError("closed")], ids=["disconnected", "closed"])
    async def test_a_message_to_a_vanished_browser_is_dropped_quietly(self, failure: Exception) -> None:
        ws = browser_socket()
        ws.send_text.side_effect = failure

        await ws_session._send_json_to_ws(ws, {"type": "error", "message": "boom"})

        ws.send_text.assert_awaited_once_with(json.dumps({"type": "error", "message": "boom"}))

    async def test_a_socket_that_cannot_be_closed_is_left_alone(self) -> None:
        ws = browser_socket()
        ws.close.side_effect = RuntimeError("already closed")

        await ws_session._close_ws(ws, 1000, "bye")

        ws.close.assert_awaited_once_with(code=1000, reason="bye")


class TestSessionRun:
    @pytest.mark.parametrize(
        ("provider", "streams_live"),
        [("deepgram", True), ("DeepGram", True), ("openai", False)],
        ids=["deepgram", "any letter case", "batch provider"],
    )
    async def test_the_provider_decides_between_live_and_batch_transcription(self, provider: str, streams_live: bool) -> None:
        session = voice_session(provider=provider)

        with (
            patch.object(_VoiceSession, "_run_streaming_stt", new_callable=AsyncMock) as live,
            patch.object(_VoiceSession, "_run_batch_stt", new_callable=AsyncMock) as batch,
        ):
            await session.run()

        assert (live.await_count, batch.await_count) == ((1, 0) if streams_live else (0, 1))

    async def test_the_session_ends_with_a_normal_close_and_leaves_no_task_behind(self) -> None:
        session = voice_session(provider="openai")
        tasks_before = asyncio.all_tasks()

        await session.run()

        socket_of(session).close.assert_awaited_once_with(code=1000, reason="Session ended")
        assert asyncio.all_tasks() == tasks_before

    async def test_the_session_is_closed_even_when_speech_to_text_crashes(self) -> None:
        session = voice_session(provider="openai")

        with patch.object(_VoiceSession, "_run_batch_stt", new_callable=AsyncMock, side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError, match="boom"):
                await session.run()

        socket_of(session).close.assert_awaited_once_with(code=1000, reason="Session ended")
