"""The Deepgram address /ws/voice/session connects to is built from the stored settings by the stt rules."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.voice.ws_session import router
from app.channels.types import VoiceConfig
from app.channels.voice.stt import deepgram_stream_url, default_stt_model

_KEYTERMS = ["alpha", " ", "beta"]


def _requested_url(config: VoiceConfig) -> str:
    app = FastAPI()
    app.include_router(router, prefix="/ws/voice")
    with (
        patch("app.api.voice.ws_session._load_voice_config", new_callable=AsyncMock, return_value=config),
        patch("websockets.connect", side_effect=ConnectionRefusedError("refused")) as connect,
        TestClient(app) as client,
        client.websocket_connect("/ws/voice/session") as ws,
    ):
        ws.send_text(json.dumps({"type": "config", "keyterms": _KEYTERMS}))
        assert json.loads(ws.receive_text())["type"] == "error"
    return str(connect.call_args.args[0])


@pytest.mark.parametrize("stt_model", ["", "nova-2"])
def test_the_session_connects_to_the_address_built_from_the_stored_settings(stt_model: str) -> None:
    config = VoiceConfig(stt_enabled=True, stt_provider="deepgram", stt_api_key="key", stt_model=stt_model, stt_language="en")

    url = _requested_url(config)

    assert url == deepgram_stream_url(stt_model, "en", _KEYTERMS)
    assert f"model={stt_model or default_stt_model('deepgram')}&" in url
