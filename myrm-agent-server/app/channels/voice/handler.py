"""Voice STT/TTS handling for inbound/outbound channel messages.

Transcribes incoming voice messages (STT) and optionally converts Agent
replies to audio (TTS). Supports Agent-driven TTS directives for
dynamic voice control.

[INPUT]
- channels.types::InboundMessage (POS: message flowing from an external channel into the system)
- channels.core.bus::MessageBus (POS: async message bus)
- channels.voice.stt::transcribe, download_audio (POS: speech-to-text)
- channels.voice.tts::synthesize (POS: text-to-speech)

[OUTPUT]
- transcribe_inbound(): STT transcription of inbound voice
- transcribe_video_inbound(): STT transcription of video audio track
- download_inbound_audio(): download inbound voice attachment
- download_inbound_video(): download inbound video attachment for STT
- maybe_tts(): outbound TTS synthesis
- has_audio_attachment(): check whether message contains audio attachment
- has_video_with_audio(): check whether message contains video with audio
- parse_tts_directives(): parse Agent TTS directives
- strip_tts_tags(): strip TTS tags

[POS]
Voice processing module: a collection of pure/utility functions invoked by AgentRouter
during message processing.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from typing import cast

from app.channels.types import (
    InboundMessage,
    MediaAttachment,
    MediaType,
    OutboundMessage,
    TTSMode,
    VoiceConfig,
)

logger = logging.getLogger(__name__)

# MessageBus.get_channel: a channel by name. Channels expose their own download method, so the result is probed by name.
type ChannelLookup = Callable[[str], object | None]

_TTS_DIRECTIVE_OPEN = "[[tts:"
_TTS_TEXT_OPEN = "[[tts:text]]"
_TTS_TEXT_CLOSE = "[[/tts:text]]"

# (start, end, body) of a marker in the text; the body is what stands between its delimiters.
_Marker = tuple[int, int, str]


def _tts_directives(text: str) -> Iterator[_Marker]:
    """Yield each ``[[tts:body]]`` whose body is not empty and holds no ``]``.

    A reply is untrusted text. Trying a pattern at every ``[[tts:`` makes a reply made of nothing else cost the square
    of its length, so the first ``]`` after an opener is looked up once and decides it: none ahead means no later
    opener can close either, and an opener that fails there is followed by the openers inside its body, which fail
    at the same ``]``.
    """
    position = 0
    while (start := text.find(_TTS_DIRECTIVE_OPEN, position)) != -1:
        body_start = start + len(_TTS_DIRECTIVE_OPEN)
        close = text.find("]", body_start)
        if close == -1:
            return
        if close > body_start and text.startswith("]]", close):
            yield start, close + 2, text[body_start:close]
            position = close + 2
        else:
            position = close


def _tts_text_blocks(text: str) -> Iterator[_Marker]:
    """Yield each ``[[tts:text]]…[[/tts:text]]``, a block ending at the first closing marker after its opener.

    With no closing marker after an opener there is none after a later one either, so the scan ends there instead of
    searching to the end of the text once per opener.
    """
    position = 0
    while (start := text.find(_TTS_TEXT_OPEN, position)) != -1:
        body_start = start + len(_TTS_TEXT_OPEN)
        close = text.find(_TTS_TEXT_CLOSE, body_start)
        if close == -1:
            return
        position = close + len(_TTS_TEXT_CLOSE)
        yield start, position, text[body_start:close]


def _without(text: str, markers: Iterable[_Marker]) -> str:
    """Return the text with the given (ordered, non-overlapping) markers cut out."""
    kept: list[str] = []
    position = 0
    for start, end, _ in markers:
        kept.append(text[position:start])
        position = end
    kept.append(text[position:])
    return "".join(kept)


def has_audio_attachment(msg: InboundMessage) -> bool:
    """Check if the message contains an audio/voice attachment."""
    return any(a.media_type == MediaType.AUDIO for a in msg.media)


def has_video_with_audio(msg: InboundMessage) -> bool:
    """Check if the message contains a video attachment that may have audio to transcribe."""
    return any(a.media_type == MediaType.VIDEO for a in msg.media)


def parse_tts_directives(text: str) -> tuple[dict[str, str], str | None]:
    """Extract TTS directives and optional TTS-only text block.

    Returns (directives_dict, tts_text_block_or_none).
    """
    directives: dict[str, str] = {}

    for _, _, body in _tts_directives(text):
        raw = body.strip()
        if raw == "off":
            directives["off"] = "true"
            continue
        if raw == "text":
            continue
        for part in raw.split():
            if "=" in part:
                key, _, val = part.partition("=")
                directives[key.strip()] = val.strip()

    first_block = next(_tts_text_blocks(text), None)
    tts_text = first_block[2].strip() if first_block else None

    return directives, tts_text


def strip_tts_tags(text: str) -> str:
    """Remove all TTS directive tags from text for display in chat."""
    text = _without(text, _tts_text_blocks(text))
    text = _without(text, _tts_directives(text))
    return text.strip()


async def transcribe_inbound(
    msg: InboundMessage,
    voice_config: VoiceConfig | None,
    get_channel_fn: ChannelLookup,
) -> InboundMessage:
    """Transcribe voice attachments and inject transcript into content.

    Best-effort: returns original message unchanged on failure.

    Args:
        msg: Inbound message to transcribe.
        voice_config: Voice configuration (STT settings).
        get_channel_fn: Callable to get channel by name (MessageBus.get_channel).
    """
    if not voice_config or not voice_config.stt_enabled:
        return msg

    from app.channels.voice.stt import transcribe

    audio_path = await download_inbound_audio(msg, get_channel_fn)
    if not audio_path:
        return msg

    try:
        result = await transcribe(audio_path, voice_config)
    finally:
        audio_path.unlink(missing_ok=True)

    if not result or not result.text:
        return msg

    prefix = f"[ Voice] {result.text}"
    content = f"{prefix}\n{msg.content}" if msg.content else prefix
    logger.warning("Voice: STT transcribed %d chars from voice", len(result.text))
    return dataclasses.replace(msg, content=content)


async def transcribe_video_inbound(
    msg: InboundMessage,
    voice_config: VoiceConfig | None,
    get_channel_fn: ChannelLookup,
) -> InboundMessage:
    """Transcribe audio from video attachments and inject transcript into content.

    Best-effort: returns original message unchanged on failure.
    Video files (.mp4, .webm) are passed directly to STT providers
    which natively support these formats.
    """
    if not voice_config or not voice_config.stt_enabled:
        return msg

    from app.channels.voice.stt import transcribe

    video_path = await download_inbound_video(msg, get_channel_fn)
    if not video_path:
        return msg

    try:
        result = await transcribe(video_path, voice_config)
    finally:
        video_path.unlink(missing_ok=True)

    if not result or not result.text:
        return msg

    prefix = f"[Video Transcript] {result.text}"
    content = f"{prefix}\n{msg.content}" if msg.content else prefix
    logger.warning("Voice: STT transcribed %d chars from video audio", len(result.text))
    return dataclasses.replace(msg, content=content)


async def download_inbound_video(
    msg: InboundMessage,
    get_channel_fn: ChannelLookup,
) -> Path | None:
    """Download a video attachment from the inbound message for STT."""
    ch = get_channel_fn(msg.channel)
    if not ch:
        return None

    if msg.channel == "telegram":
        file_id = msg.metadata.get("video_file_id")
        download = getattr(ch, "download_video_file", None)
        if isinstance(file_id, str) and download is not None:
            return cast("Path | None", await download(file_id))

    for attachment in msg.media:
        if attachment.media_type == MediaType.VIDEO:
            if attachment.path:
                return Path(attachment.path)
            if attachment.url:
                from app.channels.voice.stt import download_audio

                return await download_audio(attachment.url)

    return None


async def download_inbound_audio(
    msg: InboundMessage,
    get_channel_fn: ChannelLookup,
) -> Path | None:
    """Download the voice attachment from the inbound message.

    Dispatches to the appropriate channel's download method.
    """
    ch = get_channel_fn(msg.channel)
    if not ch:
        return None

    if msg.channel == "whatsapp":
        voice_msg_id = msg.metadata.get("voice_message_id")
        download = getattr(ch, "download_voice_message", None)
        if isinstance(voice_msg_id, str) and download is not None:
            return cast("Path | None", await download(voice_msg_id))

    if msg.channel == "telegram":
        file_id = msg.metadata.get("voice_file_id")
        download = getattr(ch, "download_voice_message", None)
        if isinstance(file_id, str) and download is not None:
            return cast("Path | None", await download(file_id))

    for attachment in msg.media:
        if attachment.media_type == MediaType.AUDIO:
            if attachment.path:
                return Path(attachment.path)
            if attachment.url:
                from app.channels.voice.stt import download_audio

                return await download_audio(attachment.url)

    return None


async def maybe_tts(
    result: OutboundMessage,
    inbound_had_voice: bool,
    voice_config: VoiceConfig | None,
) -> OutboundMessage:
    """Optionally convert the Agent reply to audio based on TTS mode.

    Supports Agent-driven TTS directives in the reply:
    - ``[[tts:off]]`` — skip TTS for this reply
    - ``[[tts:voice=xxx]]`` — override voice
    - ``[[tts:provider=xxx]]`` — override provider
    - ``[[tts:text]]...[[/tts:text]]`` — TTS-only text (stripped from chat)

    Returns the original result if TTS is disabled or fails.
    """
    if not voice_config or voice_config.tts_mode == TTSMode.OFF:
        return result
    if voice_config.tts_mode == TTSMode.INBOUND and not inbound_had_voice:
        return result
    if not result.content:
        return result

    content = result.content
    directives, tts_text_block = parse_tts_directives(content)

    if directives.get("off"):
        return dataclasses.replace(result, content=strip_tts_tags(content))

    vc = dataclasses.replace(
        voice_config,
        tts_voice=directives.get("voice", voice_config.tts_voice),
        tts_provider=directives.get("provider", voice_config.tts_provider),
    )

    tts_input = tts_text_block if tts_text_block else strip_tts_tags(content)
    clean_content = strip_tts_tags(content)

    from app.channels.voice.tts import synthesize

    audio_path = await synthesize(tts_input, vc)
    if not audio_path:
        return dataclasses.replace(result, content=clean_content)

    audio_attachment = MediaAttachment(
        media_type=MediaType.AUDIO,
        path=str(audio_path),
        mime_type="audio/mpeg",
        ephemeral=True,
    )
    logger.warning("Voice: TTS synthesized %s", audio_path.name)
    return dataclasses.replace(
        result,
        content=clean_content,
        media=result.media + (audio_attachment,),
    )
