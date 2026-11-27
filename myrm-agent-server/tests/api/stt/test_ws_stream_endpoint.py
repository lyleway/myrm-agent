"""/ws/stt/stream session setup: origin and config checks, provider routing and the voice config lookup."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import WebSocketDisconnect

from app.api.stt import ws_stream
from app.channels.types import VoiceConfig
from tests.api.stt.stream_testkit import browser_socket, sent_json, stored_voice_settings


def _config(provider: str = "deepgram", *, enabled: bool = True) -> VoiceConfig:
    return VoiceConfig(stt_enabled=enabled, stt_provider=provider, stt_api_key="key")


def _socket(config_message: str | Exception = "{}") -> AsyncMock:
    ws = browser_socket()
    ws.receive_text.side_effect = [config_message]
    return ws


@pytest.fixture(autouse=True)
def _origin_allowed() -> Iterator[None]:
    with patch.object(ws_stream, "verify_ws_origin", new_callable=AsyncMock, return_value=True):
        yield


class TestSessionSetup:
    async def test_a_refused_origin_never_gets_the_socket_accepted(self) -> None:
        ws = _socket()

        with patch.object(ws_stream, "verify_ws_origin", new_callable=AsyncMock, return_value=False):
            await ws_stream.stt_stream(ws)

        ws.accept.assert_not_awaited()

    @pytest.mark.parametrize(
        "config_message",
        [
            "not json",
            "[]",
            "null",
            '"config"',
            '{"keyterms": "alpha"}',
            '{"keyterms": [1]}',
            WebSocketDisconnect(),
            asyncio.TimeoutError(),
        ],
        ids=[
            "garbled",
            "an array",
            "null",
            "a string",
            "keyterms not a list",
            "keyterms not strings",
            "disconnected",
            "too slow",
        ],
    )
    async def test_an_unreadable_config_closes_the_session_before_any_lookup(self, config_message: str | Exception) -> None:
        ws = _socket(config_message)

        with patch.object(ws_stream, "_load_voice_config_from_ws", new_callable=AsyncMock) as load:
            await ws_stream.stt_stream(ws)

        ws.close.assert_awaited_once_with(code=1011, reason="Invalid config")
        load.assert_not_awaited()

    async def test_the_session_ends_when_no_voice_config_could_be_loaded(self) -> None:
        ws = _socket()

        with patch.object(ws_stream, "_load_voice_config_from_ws", new_callable=AsyncMock, return_value=None):
            await ws_stream.stt_stream(ws)

        ws.close.assert_awaited_once_with(code=1011, reason="STT not configured")

    async def test_an_unavailable_local_engine_is_reported_to_the_browser(self) -> None:
        ws = _socket()

        with (
            patch.object(ws_stream, "_load_voice_config_from_ws", new_callable=AsyncMock, return_value=_config("local")),
            patch("app.channels.voice.stt.local_stt_unavailable_detail", return_value="install local-stt"),
            patch.object(ws_stream, "_stream_via_deepgram", new_callable=AsyncMock) as deepgram,
            patch.object(ws_stream, "_batch_fallback", new_callable=AsyncMock) as batch,
        ):
            await ws_stream.stt_stream(ws)

        assert sent_json(ws) == [{"type": "error", "message": "install local-stt"}]
        ws.close.assert_awaited_once_with(code=1011, reason="Local STT unavailable")
        deepgram.assert_not_awaited()
        batch.assert_not_awaited()

    @pytest.mark.parametrize(("provider", "streams"), [("deepgram", True), ("Deepgram", True), ("openai", False)])
    async def test_only_deepgram_is_proxied_as_a_live_stream(self, provider: str, streams: bool) -> None:
        ws = _socket('{"keyterms": ["alpha"]}')
        config = _config(provider)

        with (
            patch.object(ws_stream, "_load_voice_config_from_ws", new_callable=AsyncMock, return_value=config),
            patch.object(ws_stream, "_stream_via_deepgram", new_callable=AsyncMock) as deepgram,
            patch.object(ws_stream, "_batch_fallback", new_callable=AsyncMock) as batch,
        ):
            await ws_stream.stt_stream(ws)

        if streams:
            deepgram.assert_awaited_once_with(ws, config, ["alpha"])
            batch.assert_not_awaited()
        else:
            batch.assert_awaited_once_with(ws, config)
            deepgram.assert_not_awaited()


class TestVoiceConfigLookup:
    async def test_strict_deployments_refuse_an_anonymous_socket(self) -> None:
        ws = browser_socket()

        with stored_voice_settings(_config(), strict_auth=True):
            loaded = await ws_stream._load_voice_config_from_ws(ws)

        assert loaded is None
        assert sent_json(ws) == [{"type": "error", "message": "Authentication required"}]
        ws.close.assert_awaited_once_with(code=1011, reason="Unauthorized")

    @pytest.mark.parametrize("user_id", ["", "alice"], ids=["local anonymous", "signed in"])
    async def test_the_stored_config_is_returned_when_stt_is_enabled(self, user_id: str) -> None:
        ws = browser_socket(user_id=user_id)
        stored = _config()

        with stored_voice_settings(stored):
            loaded = await ws_stream._load_voice_config_from_ws(ws)

        assert loaded is stored
        ws.close.assert_not_awaited()

    @pytest.mark.parametrize("extracted", [None, _config(enabled=False)], ids=["no voice settings", "stt disabled"])
    async def test_a_missing_or_disabled_stt_config_ends_the_session(self, extracted: VoiceConfig | None) -> None:
        ws = browser_socket(user_id="alice")

        with stored_voice_settings(extracted):
            loaded = await ws_stream._load_voice_config_from_ws(ws)

        assert loaded is None
        assert sent_json(ws) == [{"type": "error", "message": "STT not configured"}]
        ws.close.assert_awaited_once_with(code=1011, reason="STT not configured")

    @pytest.mark.parametrize(
        ("user_id", "logged_user"), [("alice", "alice"), ("", "sandbox")], ids=["signed in", "local anonymous"]
    )
    async def test_a_failing_config_store_is_logged_and_reported_as_not_configured(
        self, user_id: str, logged_user: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        ws = browser_socket(user_id=user_id)

        with stored_voice_settings(None, load_error=RuntimeError("db down")), caplog.at_level(logging.WARNING):
            loaded = await ws_stream._load_voice_config_from_ws(ws)

        assert loaded is None
        assert f"Failed to load voice config for WS user {logged_user}" in caplog.text
        assert sent_json(ws) == [{"type": "error", "message": "STT not configured"}]


class TestSocketHelpers:
    @pytest.mark.parametrize("failure", [WebSocketDisconnect(), RuntimeError("closed")], ids=["disconnected", "closed"])
    async def test_a_message_to_a_gone_browser_is_dropped_quietly(self, failure: Exception) -> None:
        ws = AsyncMock()
        ws.send_text.side_effect = failure

        await ws_stream._send_json(ws, {"type": "info"})

    async def test_closing_an_already_closed_socket_is_not_an_error(self) -> None:
        ws = AsyncMock()
        ws.close.side_effect = RuntimeError("already closed")

        await ws_stream._close_ws(ws, 1000, "bye")
