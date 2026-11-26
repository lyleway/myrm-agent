"""What the providers put on the wire: the model an empty setting resolves to and how the spoken language is named."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.channels.types import VoiceConfig
from app.channels.voice.stt import transcribe
from app.channels.voice.stt.models import language_fields
from tests.channels.voice.stt.stt_testkit import audio_file

_OPENAI_URL = "https://api.openai.com/v1/audio/transcriptions"
_GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
_DEEPGRAM_URL = "https://api.deepgram.com/v1/listen"
_TRANSCRIPT: dict[str, object] = {"text": "hello", "language": "en"}
_DEEPGRAM_TRANSCRIPT: dict[str, object] = {
    "results": {"channels": [{"alternatives": [{"transcript": "hello"}]}]},
    "metadata": {},
}


def _config(provider: str, *, model: str = "", language: str | None = None, base_url: str = "") -> VoiceConfig:
    return VoiceConfig(
        stt_enabled=True,
        stt_provider=provider,
        stt_api_key="key",
        stt_model=model,
        stt_language=language,
        stt_base_url=base_url,
    )


async def _posted(config: VoiceConfig, url: str, body: dict[str, object]) -> tuple[str, dict[str, object]]:
    """Transcribe a throw-away file against a mocked HTTP client; return the URL and keyword arguments of its one POST."""
    path = audio_file()
    response = httpx.Response(200, json=body, request=httpx.Request("POST", url))
    with patch("app.channels.voice.stt.providers.httpx.AsyncClient") as client_cls:
        client = MagicMock()
        client.post = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client
        try:
            result = await transcribe(path, config)
        finally:
            path.unlink(missing_ok=True)
    assert result is not None
    client.post.assert_awaited_once()
    args, kwargs = client.post.await_args
    return args[0], kwargs


class TestOpenAiCompatibleModel:
    @pytest.mark.asyncio
    async def test_an_empty_openai_model_runs_gpt_transcribe(self) -> None:
        url, sent = await _posted(_config("openai"), _OPENAI_URL, _TRANSCRIPT)

        assert url == _OPENAI_URL
        assert sent["data"] == {"model": "gpt-transcribe", "response_format": "json"}

    @pytest.mark.asyncio
    async def test_an_empty_groq_model_runs_whisper_large_v3(self) -> None:
        url, sent = await _posted(_config("groq"), _GROQ_URL, _TRANSCRIPT)

        assert url == _GROQ_URL
        assert sent["data"] == {"model": "whisper-large-v3", "response_format": "json"}

    @pytest.mark.asyncio
    async def test_a_custom_endpoint_without_a_model_gets_the_name_such_servers_recognise(self) -> None:
        endpoint = "http://localhost:8000/v1/audio/transcriptions"

        url, sent = await _posted(_config("openai", base_url="http://localhost:8000/v1/"), endpoint, _TRANSCRIPT)

        assert url == endpoint
        assert sent["data"] == {"model": "whisper-1", "response_format": "json"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["gpt-4o-transcribe", "whisper-1", "my-finetune"])
    async def test_a_model_the_user_typed_is_sent_as_typed(self, model: str) -> None:
        _, sent = await _posted(_config("openai", model=model), _OPENAI_URL, _TRANSCRIPT)

        assert sent["data"] == {"model": model, "response_format": "json"}


class TestSpokenLanguage:
    @pytest.mark.asyncio
    async def test_gpt_transcribe_names_the_language_as_a_list_and_not_as_a_single_field(self) -> None:
        _, sent = await _posted(_config("openai", language="ZH"), _OPENAI_URL, _TRANSCRIPT)

        assert sent["data"] == {"model": "gpt-transcribe", "response_format": "json", "languages[]": ["zh"]}

    @pytest.mark.asyncio
    async def test_other_models_name_the_language_as_a_single_field(self) -> None:
        _, sent = await _posted(_config("openai", model="whisper-1", language="zh"), _OPENAI_URL, _TRANSCRIPT)

        assert sent["data"] == {"model": "whisper-1", "response_format": "json", "language": "zh"}

    def test_the_language_list_is_one_repeated_form_field_on_the_wire(self) -> None:
        request = httpx.Request(
            "POST",
            _OPENAI_URL,
            data={"model": "gpt-transcribe", **language_fields("gpt-transcribe", "zh-CN")},
            files={"file": ("audio.wav", b"\x00")},
        )

        body = request.read()

        assert b'name="languages[]"\r\n\r\nzh-cn\r\n' in body
        assert b'name="language"' not in body


class TestDeepgramModel:
    @pytest.mark.asyncio
    async def test_an_empty_deepgram_model_runs_nova_3(self) -> None:
        _, sent = await _posted(_config("deepgram"), _DEEPGRAM_URL, _DEEPGRAM_TRANSCRIPT)

        assert sent["params"]["model"] == "nova-3"

    @pytest.mark.asyncio
    async def test_a_typed_deepgram_model_is_sent_as_typed(self) -> None:
        _, sent = await _posted(_config("deepgram", model="nova-2"), _DEEPGRAM_URL, _DEEPGRAM_TRANSCRIPT)

        assert sent["params"]["model"] == "nova-2"
