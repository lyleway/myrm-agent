"""xAI Grok speech-to-text: which API key is used and what the upload request carries."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from myrm_agent_harness.agent.security import EphemeralUserCredential, user_credentials_ctx

from app.channels.types import STTResult, VoiceConfig
from app.channels.voice.stt import transcribe
from app.channels.voice.stt.providers import _resolve_xai_stt_api_key
from tests.channels.voice.stt.stt_testkit import audio_file, mocked_http_client

_XAI_STT_URL = "https://api.x.ai/v1/stt"
_AUDIO = b"\x00" * 2048


def _config(*, api_key: str = "xai-key", language: str | None = None, base_url: str = "") -> VoiceConfig:
    return VoiceConfig(stt_enabled=True, stt_provider="xai", stt_api_key=api_key, stt_language=language, stt_base_url=base_url)


def _response(body: dict[str, object], status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body, request=httpx.Request("POST", _XAI_STT_URL))


@contextmanager
def _signed_in(*credentials: EphemeralUserCredential) -> Iterator[None]:
    reset_token = user_credentials_ctx.set(credentials)
    try:
        yield
    finally:
        user_credentials_ctx.reset(reset_token)


def _upload(
    client: AsyncMock, *, url: str = _XAI_STT_URL, data: dict[str, str] | None = None
) -> dict[str, tuple[str, object, str]]:
    """Assert the request line, bearer header and form fields of the one POST made; return its multipart files."""
    request = client.post.await_args
    assert request.args == (url,)
    assert request.kwargs["headers"] == {"Authorization": "Bearer xai-key"}
    assert request.kwargs["data"] == (data or {"format": "true"})
    return request.kwargs["files"]


class TestUpload:
    async def test_a_file_is_posted_as_multipart_with_the_bearer_key(self) -> None:
        path = audio_file(suffix=".ogg")
        try:
            with mocked_http_client(_response({"text": " Hallo Welt ", "language": "de", "duration": 1.5})) as client:
                result = await transcribe(path, _config())
        finally:
            path.unlink(missing_ok=True)

        assert result == STTResult(text="Hallo Welt", language="de", duration=1.5)
        name, _, mime = _upload(client)["file"]
        assert (name, mime) == (path.name, "audio/ogg")

    async def test_audio_bytes_are_posted_as_a_wav_upload(self) -> None:
        with mocked_http_client(_response({"text": "hi"})) as client:
            result = await transcribe(None, _config(), audio_bytes=_AUDIO)

        assert result == STTResult(text="hi")
        assert _upload(client) == {"file": ("audio.wav", _AUDIO, "audio/wav")}

    async def test_the_language_and_a_custom_base_url_reach_the_request(self) -> None:
        config = _config(language="fr", base_url="https://gateway.example/v1/")

        with mocked_http_client(_response({"text": "bonjour", "language": "fr"})) as client:
            await transcribe(None, config, audio_bytes=_AUDIO)

        _upload(client, url="https://gateway.example/v1/stt", data={"language": "fr", "format": "true"})


class TestFailures:
    @pytest.mark.parametrize(
        ("response", "failure"),
        [
            (_response({"text": "   "}), "Empty transcript returned from xAI STT"),
            (_response({"error": "bad key"}, status=401), "401"),
        ],
        ids=["no text in the answer", "rejected upload"],
    )
    async def test_a_failed_request_is_no_transcript(
        self, response: httpx.Response, failure: str, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            patch("app.channels.voice.stt.core.is_local_available", return_value=False),
            mocked_http_client(response),
            caplog.at_level(logging.ERROR),
        ):
            result = await transcribe(None, _config(), audio_bytes=_AUDIO)

        assert result is None
        assert "primary provider 'xai' failed" in caplog.text
        assert failure in caplog.text


class TestApiKeyResolution:
    def test_an_explicit_key_wins_over_the_signed_in_account(self) -> None:
        with _signed_in(EphemeralUserCredential(issuer="xai", token="oauth-token")):
            assert _resolve_xai_stt_api_key(_config(api_key="explicit")) == "explicit"

    def test_without_a_key_the_signed_in_xai_account_is_used(self) -> None:
        with _signed_in(
            EphemeralUserCredential(issuer="github", token="gh-token"),
            EphemeralUserCredential(issuer="xai", token="oauth-token"),
        ):
            assert _resolve_xai_stt_api_key(_config(api_key="")) == "oauth-token"

    @pytest.mark.parametrize(
        "credentials",
        [
            (),
            (EphemeralUserCredential(issuer="github", token="gh-token"),),
            (EphemeralUserCredential(issuer="xai", token="   "),),
        ],
        ids=["nobody signed in", "only another provider", "blank xai token"],
    )
    def test_an_account_without_a_usable_xai_token_leaves_the_key_empty(
        self, credentials: tuple[EphemeralUserCredential, ...]
    ) -> None:
        with _signed_in(*credentials):
            assert _resolve_xai_stt_api_key(_config(api_key="")) == ""

    def test_an_unavailable_credential_source_never_breaks_the_call(self) -> None:
        with patch.dict("sys.modules", {"app.services.agent.session_credential_assembler": None}):
            assert _resolve_xai_stt_api_key(_config(api_key="")) == ""
