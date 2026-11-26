"""Tests for the voice handler (handler.py): audio attachment detection, TTS directives and mode logic, inbound transcription.

The STT and TTS packages have their own tests under tests/channels/voice/.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.channels.types import (
    InboundMessage,
    MediaAttachment,
    MediaType,
    OutboundMessage,
    STTResult,
    TTSMode,
    VoiceConfig,
)
from app.channels.voice.handler import (
    download_inbound_audio,
    has_audio_attachment,
    maybe_tts,
    parse_tts_directives,
    strip_tts_tags,
    transcribe_inbound,
)
from tests.channels.voice.voice_testkit import voice_config


def _msg(
    content: str = "hello",
    media: tuple[MediaAttachment, ...] = (),
    channel: str = "test",
    metadata: dict[str, object] | None = None,
) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id="user-1",
        content=content,
        chat_id="chat-1",
        media=media,
        metadata=metadata or {},
    )


def _outbound(content: str = "reply") -> OutboundMessage:
    return OutboundMessage(channel="test", recipient_id="chat-1", content=content, user_id="u1")


def _audio_attachment(url: str | None = None, path: str | None = None) -> MediaAttachment:
    return MediaAttachment(media_type=MediaType.AUDIO, url=url, path=path)


# ══════════════════════════════════════════════════════════════════════
# handler.py — Pure Functions
# ══════════════════════════════════════════════════════════════════════


class TestHasAudioAttachment:
    def test_no_media(self) -> None:
        assert has_audio_attachment(_msg()) is False

    def test_image_only(self) -> None:
        img = MediaAttachment(media_type=MediaType.IMAGE, url="https://x.com/img.png")
        assert has_audio_attachment(_msg(media=(img,))) is False

    def test_audio_present(self) -> None:
        assert has_audio_attachment(_msg(media=(_audio_attachment(url="https://x.com/a.mp3"),))) is True

    def test_mixed_media(self) -> None:
        img = MediaAttachment(media_type=MediaType.IMAGE, url="https://x.com/img.png")
        audio = _audio_attachment(url="https://x.com/a.mp3")
        assert has_audio_attachment(_msg(media=(img, audio))) is True


class TestParseTtsDirectives:
    def test_no_directives(self) -> None:
        directives, text_block = parse_tts_directives("Hello world")
        assert directives == {}
        assert text_block is None

    def test_off_directive(self) -> None:
        directives, _ = parse_tts_directives("Hello [[tts:off]] world")
        assert directives == {"off": "true"}

    def test_voice_directive(self) -> None:
        directives, _ = parse_tts_directives("Hello [[tts:voice=nova]] world")
        assert directives["voice"] == "nova"

    def test_provider_directive(self) -> None:
        directives, _ = parse_tts_directives("Hello [[tts:provider=elevenlabs]] world")
        assert directives["provider"] == "elevenlabs"

    def test_multiple_directives(self) -> None:
        directives, _ = parse_tts_directives("Hello [[tts:voice=nova provider=elevenlabs]] world")
        assert directives["voice"] == "nova"
        assert directives["provider"] == "elevenlabs"

    def test_text_block(self) -> None:
        _, text_block = parse_tts_directives("Hello [[tts:text]]Custom speech[[/tts:text]] world")
        assert text_block == "Custom speech"

    def test_text_block_multiline(self) -> None:
        _, text_block = parse_tts_directives("Start [[tts:text]]Line1\nLine2[[/tts:text]] end")
        assert text_block == "Line1\nLine2"


class TestStripTtsTags:
    def test_no_tags(self) -> None:
        assert strip_tts_tags("Hello world") == "Hello world"

    def test_strip_directive(self) -> None:
        assert strip_tts_tags("Hello [[tts:off]] world") == "Hello  world"

    def test_strip_text_block(self) -> None:
        result = strip_tts_tags("Hello [[tts:text]]hidden[[/tts:text]] world")
        assert "hidden" not in result
        assert "Hello" in result

    def test_strip_all(self) -> None:
        result = strip_tts_tags("[[tts:voice=nova]] Hi [[tts:text]]speech[[/tts:text]]")
        assert result == "Hi"


# ══════════════════════════════════════════════════════════════════════
# handler.py — maybe_tts
# ══════════════════════════════════════════════════════════════════════


class TestMaybeTts:
    @pytest.mark.asyncio
    async def test_tts_off_returns_original(self) -> None:
        config = voice_config(tts_mode=TTSMode.OFF)
        result = await maybe_tts(_outbound(), True, config)
        assert result.content == "reply"

    @pytest.mark.asyncio
    async def test_no_config_returns_original(self) -> None:
        result = await maybe_tts(_outbound(), True, None)
        assert result.content == "reply"

    @pytest.mark.asyncio
    async def test_inbound_mode_no_voice_returns_original(self) -> None:
        config = voice_config(tts_mode=TTSMode.INBOUND)
        result = await maybe_tts(_outbound(), False, config)
        assert result.content == "reply"

    @pytest.mark.asyncio
    async def test_empty_content_returns_original(self) -> None:
        config = voice_config(tts_mode=TTSMode.ALWAYS)
        result = await maybe_tts(_outbound(content=""), True, config)
        assert result.content == ""

    @pytest.mark.asyncio
    async def test_tts_off_directive_strips_tags(self) -> None:
        config = voice_config(tts_mode=TTSMode.ALWAYS)
        result = await maybe_tts(_outbound(content="Hello [[tts:off]] world"), True, config)
        assert "[[tts:" not in result.content

    @pytest.mark.asyncio
    async def test_successful_synthesis_adds_audio(self) -> None:
        config = voice_config(tts_mode=TTSMode.ALWAYS)
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(b"fake audio data")
        tmp.close()
        audio_path = Path(tmp.name)
        try:
            with patch(
                "app.channels.voice.tts.synthesize",
                new_callable=AsyncMock,
                return_value=audio_path,
            ):
                result = await maybe_tts(_outbound(content="Hello world test"), True, config)
                assert any(a.media_type == MediaType.AUDIO for a in result.media)
        finally:
            audio_path.unlink(missing_ok=True)

    @pytest.mark.asyncio
    async def test_synthesis_failure_returns_clean_content(self) -> None:
        config = voice_config(tts_mode=TTSMode.ALWAYS)
        with patch(
            "app.channels.voice.tts.synthesize",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await maybe_tts(_outbound(content="Hello world test"), True, config)
            assert len(result.media) == 0

    @pytest.mark.asyncio
    async def test_voice_override_from_directive(self) -> None:
        config = voice_config(tts_mode=TTSMode.ALWAYS)
        with patch(
            "app.channels.voice.tts.synthesize",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_synth:
            await maybe_tts(_outbound(content="Hello [[tts:voice=nova]] world test"), True, config)
            called_config: VoiceConfig = mock_synth.call_args[1].get("config") or mock_synth.call_args[0][1]
            assert called_config.tts_voice == "nova"

    @pytest.mark.asyncio
    async def test_tts_text_block_used_for_synthesis(self) -> None:
        config = voice_config(tts_mode=TTSMode.ALWAYS)
        with patch(
            "app.channels.voice.tts.synthesize",
            new_callable=AsyncMock,
            return_value=None,
        ) as mock_synth:
            await maybe_tts(
                _outbound(content="Display text [[tts:text]]Speech only text here[[/tts:text]]"),
                True,
                config,
            )
            tts_input = mock_synth.call_args[0][0]
            assert tts_input == "Speech only text here"


# ══════════════════════════════════════════════════════════════════════
# handler.py — transcribe_inbound
# ══════════════════════════════════════════════════════════════════════


class TestTranscribeInbound:
    @pytest.mark.asyncio
    async def test_no_config_returns_original(self) -> None:
        result = await transcribe_inbound(_msg(), None, MagicMock())
        assert result.content == "hello"

    @pytest.mark.asyncio
    async def test_stt_disabled_returns_original(self) -> None:
        config = voice_config(stt_enabled=False)
        result = await transcribe_inbound(_msg(), config, MagicMock())
        assert result.content == "hello"

    @pytest.mark.asyncio
    async def test_no_audio_returns_original(self) -> None:
        config = voice_config()
        with patch(
            "app.channels.voice.handler.download_inbound_audio",
            new_callable=AsyncMock,
            return_value=None,
        ):
            result = await transcribe_inbound(_msg(), config, MagicMock())
            assert result.content == "hello"

    @pytest.mark.asyncio
    async def test_successful_transcription(self) -> None:
        config = voice_config()
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(b"fake audio")
        tmp.close()
        audio_path = Path(tmp.name)

        stt_result = STTResult(text="transcribed text", language="en")
        msg = _msg(content="", media=(_audio_attachment(path=str(audio_path)),))
        with patch(
            "app.channels.voice.stt.transcribe",
            new_callable=AsyncMock,
            return_value=stt_result,
        ):
            result = await transcribe_inbound(msg, config, lambda _: MagicMock())
            assert "transcribed text" in result.content
            assert "" in result.content

    @pytest.mark.asyncio
    async def test_empty_transcription_returns_original(self) -> None:
        config = voice_config()
        tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        tmp.write(b"fake audio")
        tmp.close()
        audio_path = Path(tmp.name)

        msg = _msg(media=(_audio_attachment(path=str(audio_path)),))
        with patch(
            "app.channels.voice.stt.transcribe",
            new_callable=AsyncMock,
            return_value=STTResult(text=""),
        ):
            result = await transcribe_inbound(msg, config, lambda _: MagicMock())
            assert result.content == "hello"


# ══════════════════════════════════════════════════════════════════════
# handler.py — download_inbound_audio
# ══════════════════════════════════════════════════════════════════════


class TestDownloadInboundAudio:
    @pytest.mark.asyncio
    async def test_no_channel_returns_none(self) -> None:
        result = await download_inbound_audio(_msg(), lambda _: None)
        assert result is None

    @pytest.mark.asyncio
    async def test_whatsapp_voice_message(self) -> None:
        ch = MagicMock()
        ch.download_voice_message = AsyncMock(return_value=Path("/tmp/voice.ogg"))
        msg = _msg(channel="whatsapp", metadata={"voice_message_id": "wamid123"})
        result = await download_inbound_audio(msg, lambda _: ch)
        assert result == Path("/tmp/voice.ogg")

    @pytest.mark.asyncio
    async def test_telegram_voice_file(self) -> None:
        ch = MagicMock()
        ch.download_voice_message = AsyncMock(return_value=Path("/tmp/voice.ogg"))
        msg = _msg(channel="telegram", metadata={"voice_file_id": "file123"})
        result = await download_inbound_audio(msg, lambda _: ch)
        assert result == Path("/tmp/voice.ogg")

    @pytest.mark.asyncio
    async def test_audio_attachment_with_path(self) -> None:
        audio = _audio_attachment(path="/tmp/local.mp3")
        msg = _msg(media=(audio,))
        ch = MagicMock()
        result = await download_inbound_audio(msg, lambda _: ch)
        assert result == Path("/tmp/local.mp3")

    @pytest.mark.asyncio
    async def test_audio_attachment_with_url(self) -> None:
        audio = _audio_attachment(url="https://example.com/audio.mp3")
        msg = _msg(media=(audio,))
        ch = MagicMock()
        with patch(
            "app.channels.voice.stt.download_audio",
            new_callable=AsyncMock,
            return_value=Path("/tmp/downloaded.mp3"),
        ):
            result = await download_inbound_audio(msg, lambda _: ch)
            assert result == Path("/tmp/downloaded.mp3")
