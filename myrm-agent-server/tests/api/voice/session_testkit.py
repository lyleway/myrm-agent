"""Builders for the voice session handler tests: a session over a scripted browser socket and the frames it receives."""

from __future__ import annotations

from collections.abc import Sequence
from unittest.mock import AsyncMock

from app.api.voice.agent_bridge import VoiceAgentBridge
from app.api.voice.ws_session import _VoiceSession
from app.channels.types import VoiceConfig
from tests.api.stt.stream_testkit import Frame, browser_socket


def voice_config(provider: str = "deepgram") -> VoiceConfig:
    return VoiceConfig(stt_enabled=True, stt_provider=provider, stt_api_key="key")


def voice_session(
    frames: Sequence[Frame] = (),
    *,
    provider: str = "deepgram",
    bridge: VoiceAgentBridge | None = None,
    hang: bool = False,
) -> _VoiceSession:
    """A session over a browser socket that sends `frames`, then disconnects (or, with `hang`, falls silent)."""
    return _VoiceSession(
        ws=browser_socket(frames, hang=hang), voice_config=voice_config(provider), keyterms=[], agent_bridge=bridge
    )


def socket_of(session: _VoiceSession) -> AsyncMock:
    """The scripted browser socket `voice_session` put under `session`, for asserting on what the session sent."""
    socket = session._ws
    assert isinstance(socket, AsyncMock), "voice_session builds its sessions over browser_socket"
    return socket
