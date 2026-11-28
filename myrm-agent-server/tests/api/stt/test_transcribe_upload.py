"""POST /stt/transcribe takes a recording the way a browser sends it: its media type carries the codec as a parameter."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import Response

from app.api.stt.router import router
from app.channels.types import STTResult, VoiceConfig

_RECORDING = b"x" * 2048
_CLOUD_VOICE = VoiceConfig(stt_enabled=True, stt_provider="openai", stt_api_key="sk-test")


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router, prefix="/stt")
    return TestClient(app)


def _upload(client: TestClient, media_type: str) -> Response:
    return client.post("/stt/transcribe", files={"file": ("recording.webm", BytesIO(_RECORDING), media_type)})


class TestMediaType:
    @pytest.mark.parametrize(
        ("media_type", "suffix"),
        [
            ("audio/webm", ".webm"),
            # What Chrome's MediaRecorder reports for the recording the dictation button uploads.
            ("audio/webm;codecs=opus", ".webm"),
            ("audio/webm; codecs=opus", ".webm"),
            ("Audio/WebM;Codecs=Opus", ".webm"),
            ("audio/ogg;codecs=opus", ".ogg"),
        ],
    )
    def test_a_recording_is_transcribed_whatever_parameters_its_media_type_carries(
        self, client: TestClient, media_type: str, suffix: str
    ) -> None:
        with (
            patch("app.api.stt.router._load_user_voice_config", new_callable=AsyncMock, return_value=_CLOUD_VOICE),
            patch(
                "app.channels.voice.stt.transcribe", new_callable=AsyncMock, return_value=STTResult(text="hello")
            ) as transcribe,
        ):
            response = _upload(client, media_type)

        assert response.status_code == 200, response.text
        assert response.json() == {"text": "hello", "language": None, "duration": None}
        assert transcribe.await_args.args[0].suffix == suffix

    def test_a_media_type_that_is_not_audio_is_refused_by_its_type_alone(self, client: TestClient) -> None:
        with (
            patch("app.api.stt.router._load_user_voice_config", new_callable=AsyncMock, return_value=_CLOUD_VOICE),
            patch("app.channels.voice.stt.transcribe", new_callable=AsyncMock) as transcribe,
        ):
            response = _upload(client, "text/plain;charset=utf-8")

        assert response.status_code == 400
        assert response.json()["detail"] == "Unsupported audio format: text/plain"
        transcribe.assert_not_awaited()
