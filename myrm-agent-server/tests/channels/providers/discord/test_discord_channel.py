"""Tests for DiscordChannel lifecycle and inbound messages: gateway start/stop, mention handling and attachment extraction."""

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("discord")
import discord

from app.channels.providers.discord.channel import (
    DiscordChannel,
)
from app.channels.providers.discord.config import (
    DiscordChannelConfig,
)
from app.channels.types import ChannelStatus
from app.channels.types.messages import MediaType


@pytest.mark.asyncio
async def test_start_with_gateway(channel):
    with patch("discord.Client") as mock_client_cls:
        mock_client_instance = mock_client_cls.return_value
        mock_client_instance.start = AsyncMock()

        await channel.start()

        assert channel.status == ChannelStatus.IDLE
        mock_client_cls.assert_called_once()
        # The task is created, we can't easily assert on it without awaiting it,
        # but we know it should have called start
        assert channel._gateway_task is not None


@pytest.mark.asyncio
async def test_start_without_gateway():
    config = DiscordChannelConfig(bot_token="test_token", enable_gateway=False)
    channel = DiscordChannel(config)

    await channel.start()

    assert channel.status == ChannelStatus.RUNNING
    assert channel._client is None
    assert channel._gateway_task is None


@pytest.mark.asyncio
async def test_stop(channel):
    channel._client = MagicMock()
    channel._client.is_closed.return_value = False
    channel._client.close = AsyncMock()
    session = asyncio.create_task(asyncio.sleep(3600))
    channel._gateway_task = session
    await asyncio.sleep(0)

    await channel.stop()

    assert channel.status == ChannelStatus.STOPPED
    channel._client.close.assert_called_once()
    assert session.cancelled()


@pytest.mark.asyncio
async def test_stop_completes_when_the_gateway_session_already_failed(channel, caplog):
    """A session that already ended with an error (a rejected token) must not make stop() raise."""

    async def failed_session() -> None:
        raise discord.LoginFailure("Improper token has been passed.")

    channel._client = MagicMock()
    channel._client.is_closed.return_value = False
    channel._client.close = AsyncMock()
    channel._gateway_task = asyncio.create_task(failed_session())
    await asyncio.sleep(0)

    with caplog.at_level(logging.WARNING, logger="app.channels.providers.discord.channel"):
        await channel.stop()

    assert channel.status == ChannelStatus.STOPPED
    channel._client.close.assert_called_once()
    assert "LoginFailure" in caplog.text


@pytest.mark.asyncio
async def test_on_message_dispatches(channel):
    """_on_message should dispatch non-bot messages via _emit_inbound."""
    mock_message = MagicMock()
    mock_message.author.bot = False
    mock_message.author.id = 123
    mock_message.author.display_name = "test_user"
    mock_message.guild = None
    mock_message.channel.id = 789
    mock_message.id = 101
    mock_message.content = "hello"
    mock_message.attachments = []
    mock_message.reference = None
    mock_message.mentions = []

    received = []

    async def handler(msg):
        received.append(msg)

    channel.set_inbound_handler(handler)

    await channel._on_message(mock_message)

    assert len(received) == 1
    assert received[0].content == "hello"
    assert received[0].sender_id == "123"


@pytest.mark.asyncio
async def test_on_message_ignores_bot(channel):
    """_on_message should skip bot messages."""
    mock_message = MagicMock()
    mock_message.author.bot = True
    mock_message.author.id = 999

    received = []

    async def handler(msg):
        received.append(msg)

    channel.set_inbound_handler(handler)

    await channel._on_message(mock_message)

    assert len(received) == 0


@pytest.mark.asyncio
async def test_on_message_group_mention_stripped_and_metadata_injected(channel):
    """_on_message in group should strip @bot mention and inject explicit_mention metadata."""
    from app.channels.types import METADATA_EXPLICIT_MENTION_KEY

    bot_user = MagicMock()
    bot_user.id = 42
    channel._client = MagicMock()
    channel._client.user = bot_user

    mock_message = MagicMock()
    mock_message.author.bot = False
    mock_message.author.id = 123
    mock_message.author.display_name = "alice"
    mock_message.guild = MagicMock()
    mock_message.guild.id = 999
    mock_message.channel.id = 789
    mock_message.id = 105
    mock_message.content = "<@!42> help me with coding"
    mock_message.attachments = []
    mock_message.reference = None
    mock_message.mentions = [bot_user]

    received = []

    async def handler(msg):
        received.append(msg)

    channel.set_inbound_handler(handler)

    await channel._on_message(mock_message)

    assert len(received) == 1
    inbound = received[0]
    assert inbound.content == "help me with coding"
    assert inbound.is_group is True
    assert inbound.mentioned is True
    assert inbound.metadata.get(METADATA_EXPLICIT_MENTION_KEY) == "1"


# ── Media extraction tests ──


def _make_attachment(*, content_type: str, filename: str, width=None, height=None, url="https://cdn.discord.com/file"):
    att = MagicMock()
    att.content_type = content_type
    att.filename = filename
    att.width = width
    att.height = height
    att.url = url
    return att


@pytest.mark.asyncio
async def test_on_message_with_image_attachment(channel):
    """Image attachments should be extracted as MediaType.IMAGE."""
    mock_message = MagicMock()
    mock_message.author.bot = False
    mock_message.author.id = 123
    mock_message.author.display_name = "user"
    mock_message.guild = None
    mock_message.channel.id = 789
    mock_message.id = 102
    mock_message.content = "check this image"
    mock_message.attachments = [
        _make_attachment(content_type="image/png", filename="screenshot.png", width=800, height=600),
    ]

    received = []

    async def handler(msg):
        received.append(msg)

    channel.set_inbound_handler(handler)
    await channel._on_message(mock_message)

    assert len(received) == 1
    inbound = received[0]
    assert inbound.content == "check this image"
    assert len(inbound.media) == 1
    assert inbound.media[0].media_type == MediaType.IMAGE
    assert inbound.media[0].filename == "screenshot.png"
    assert inbound.media[0].url == "https://cdn.discord.com/file"


@pytest.mark.asyncio
async def test_on_message_with_document_attachment(channel):
    """Non-image/video/audio files should be MediaType.DOCUMENT."""
    mock_message = MagicMock()
    mock_message.author.bot = False
    mock_message.author.id = 123
    mock_message.author.display_name = "user"
    mock_message.guild = None
    mock_message.channel.id = 789
    mock_message.id = 103
    mock_message.content = ""
    mock_message.attachments = [
        _make_attachment(content_type="application/pdf", filename="report.pdf"),
    ]

    received = []

    async def handler(msg):
        received.append(msg)

    channel.set_inbound_handler(handler)
    await channel._on_message(mock_message)

    assert len(received) == 1
    inbound = received[0]
    assert inbound.media[0].media_type == MediaType.DOCUMENT
    assert inbound.media[0].filename == "report.pdf"
    assert inbound.media[0].mime_type == "application/pdf"


@pytest.mark.asyncio
async def test_on_message_with_multiple_attachments(channel):
    """Multiple attachments of different types should all be extracted."""
    mock_message = MagicMock()
    mock_message.author.bot = False
    mock_message.author.id = 123
    mock_message.author.display_name = "user"
    mock_message.guild = None
    mock_message.channel.id = 789
    mock_message.id = 104
    mock_message.content = "mixed media"
    mock_message.attachments = [
        _make_attachment(content_type="image/jpeg", filename="photo.jpg", width=1920, height=1080),
        _make_attachment(content_type="video/mp4", filename="clip.mp4"),
        _make_attachment(content_type="audio/ogg", filename="voice.ogg"),
        _make_attachment(content_type="text/plain", filename="notes.txt"),
    ]

    received = []

    async def handler(msg):
        received.append(msg)

    channel.set_inbound_handler(handler)
    await channel._on_message(mock_message)

    assert len(received) == 1
    inbound = received[0]
    assert len(inbound.media) == 4
    assert inbound.media[0].media_type == MediaType.IMAGE
    assert inbound.media[1].media_type == MediaType.VIDEO
    assert inbound.media[2].media_type == MediaType.AUDIO
    assert inbound.media[3].media_type == MediaType.DOCUMENT


@pytest.mark.asyncio
async def test_on_message_no_attachments(channel):
    """Messages without attachments should have empty media tuple."""
    mock_message = MagicMock()
    mock_message.author.bot = False
    mock_message.author.id = 123
    mock_message.author.display_name = "user"
    mock_message.guild = None
    mock_message.channel.id = 789
    mock_message.id = 105
    mock_message.content = "just text"
    mock_message.attachments = []

    received = []

    async def handler(msg):
        received.append(msg)

    channel.set_inbound_handler(handler)
    await channel._on_message(mock_message)

    assert len(received) == 1
    assert received[0].media == ()


@pytest.mark.asyncio
async def test_on_message_image_by_dimensions_fallback(channel):
    """Images detected by width/height even without standard content_type."""
    mock_message = MagicMock()
    mock_message.author.bot = False
    mock_message.author.id = 123
    mock_message.author.display_name = "user"
    mock_message.guild = None
    mock_message.channel.id = 789
    mock_message.id = 106
    mock_message.content = ""
    mock_message.attachments = [
        _make_attachment(content_type="", filename="unknown.bin", width=640, height=480),
    ]

    received = []

    async def handler(msg):
        received.append(msg)

    channel.set_inbound_handler(handler)
    await channel._on_message(mock_message)

    assert len(received) == 1
    assert received[0].media[0].media_type == MediaType.IMAGE
