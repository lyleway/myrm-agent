"""Builders shared by the Discord send and inbound tests: an outbound message, a ``DiscordChannel`` on a mocked client and a mock messageable."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("discord")
import discord

from app.channels.providers.discord.channel import DiscordChannel
from app.channels.providers.discord.config import DiscordChannelConfig
from app.channels.types import OutboundMessage, ToolStep


def make_msg(
    content: str = "Hello",
    reasoning: str | None = None,
    tool_steps: tuple[ToolStep, ...] = (),
    metadata: dict[str, object] | None = None,
) -> OutboundMessage:
    return OutboundMessage(
        channel="discord",
        recipient_id="123",
        content=content,
        reasoning=reasoning,
        user_id="u1",
        tool_steps=tool_steps,
        metadata=metadata,
    )


def make_mock_channel() -> tuple[DiscordChannel, MagicMock]:
    """Create a DiscordChannel with a mocked discord.Client."""
    ch = DiscordChannel(config=DiscordChannelConfig(bot_token="test-token"))
    mock_client = MagicMock(spec=discord.Client)
    mock_client.is_ready.return_value = True
    mock_client.is_closed.return_value = False
    mock_client.guilds = []

    mock_user = MagicMock()
    mock_user.id = 999
    mock_user.name = "TestBot"
    mock_client.user = mock_user

    ch._client = mock_client
    return ch, mock_client


def make_mock_messageable() -> MagicMock:
    """Create a mock Messageable channel."""
    mock_ch = MagicMock(spec=discord.TextChannel)
    mock_msg = MagicMock()
    mock_msg.id = 42
    mock_ch.send = AsyncMock(return_value=mock_msg)
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_msg.edit = AsyncMock()
    mock_msg.delete = AsyncMock()
    mock_msg.add_reaction = AsyncMock()
    mock_ch.typing = AsyncMock()
    return mock_ch
