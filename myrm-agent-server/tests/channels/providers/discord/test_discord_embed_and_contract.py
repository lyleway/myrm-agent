"""DiscordChannel contract compliance, Embed unit tests, edit overrides and inbound messages."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("discord")
import discord

from app.channels.core.base import BaseChannel
from app.channels.providers.discord.channel import (
    DiscordChannel,
)
from app.channels.providers.discord.config import DiscordChannelConfig
from app.channels.providers.discord.helpers import build_discord_embed
from app.channels.types import InboundMessage, ToolStep
from tests.channels.channel_test_base import ChannelTestBase
from tests.channels.providers.discord.send_testkit import make_mock_channel, make_msg


class TestDiscordChannelContract(ChannelTestBase):
    def create_channel(self) -> BaseChannel:
        return DiscordChannel(
            config=DiscordChannelConfig(
                bot_token="test-discord-bot-token-for-unit-test",
            )
        )


class TestBuildEmbed:
    """Unit tests for the _build_embed pure function."""

    def test_basic_embed(self) -> None:
        embed = build_discord_embed(make_msg())
        assert embed is None

    def test_basic_embed_with_reasoning(self) -> None:
        embed = build_discord_embed(make_msg(reasoning="Hello world"))
        assert embed is not None
        assert embed.fields[0].value == "```\nHello world\n```"
        assert embed.color is not None
        assert embed.color.value == discord.Color.blurple().value
        assert len(embed.fields) == 1

    def test_description_truncation(self) -> None:
        long_text = "x" * (4096 + 500)
        embed = build_discord_embed(make_msg(reasoning=long_text))
        assert embed is not None
        value = embed.fields[0].value
        assert value is not None
        assert len(value) == 4090 + 3 + 8  # 4090 + "..." + "```\n\n```"

    def test_tool_steps_as_fields(self) -> None:
        steps = (
            ToolStep(name="search", label="Web Search", detail="Found 5 results"),
            ToolStep(name="read", label="Read File", detail=None),
        )
        embed = build_discord_embed(make_msg(tool_steps=steps))
        assert embed is not None
        assert len(embed.fields) == 1
        assert embed.fields[0].name == " Tool Execution"
        value = embed.fields[0].value
        assert value is not None
        assert "**Web Search**: Found 5 results" in value
        assert "Read File" in value

    def test_tool_steps_max_25(self) -> None:
        steps = tuple(ToolStep(name=f"t{i}", label=f"Tool {i}") for i in range(30))
        embed = build_discord_embed(make_msg(tool_steps=steps))
        assert embed is not None
        assert len(embed.fields) == 1

    def test_tool_step_detail_truncation(self) -> None:
        long_detail = "d" * (1024 + 100)
        steps = (ToolStep(name="t", label="T", detail=long_detail),)
        embed = build_discord_embed(make_msg(tool_steps=steps))
        assert embed is not None
        value = embed.fields[0].value
        assert value is not None
        assert len(value) <= 1024

    def test_no_sources_field(self) -> None:
        """Sources are handled by render pipeline, not by build_discord_embed."""
        metadata: dict[str, object] = {"sources": [{"url": "https://example.com", "title": "Example"}]}
        embed = build_discord_embed(make_msg(metadata=metadata))
        assert embed is None

    def test_empty_content(self) -> None:
        embed = build_discord_embed(make_msg(content=""))
        assert embed is None


class TestDiscordEdits:
    """An edit reaches Discord through the channel's own methods, not the base channel's."""

    def test_edit_message_and_edit_placeholder_message_are_overridden(self) -> None:
        assert DiscordChannel.edit_message is not BaseChannel.edit_message
        assert DiscordChannel.edit_placeholder_message is not BaseChannel.edit_placeholder_message


class TestDiscordInbound:
    """Tests for inbound message handling with mocked discord events."""

    @pytest.mark.asyncio
    async def test_on_message_emits_inbound(self) -> None:
        ch, client = make_mock_channel()
        received: list[InboundMessage] = []

        async def handler(msg: InboundMessage) -> None:
            received.append(msg)

        ch.set_inbound_handler(handler)

        mock_msg = MagicMock(spec=discord.Message)
        mock_msg.author = MagicMock()
        mock_msg.author.bot = False
        mock_msg.author.id = 123
        mock_msg.author.display_name = "TestUser"
        mock_msg.guild = None
        mock_msg.channel = MagicMock()
        mock_msg.channel.id = 456
        mock_msg.content = "Hello bot"
        mock_msg.attachments = []
        mock_msg.reference = None
        mock_msg.id = 789
        mock_msg.mentions = []

        await ch._on_message(mock_msg)
        assert len(received) == 1
        assert received[0].content == "Hello bot"
        assert received[0].sender_id == "123"
        assert received[0].sender_name == "TestUser"

    @pytest.mark.asyncio
    async def test_on_message_filters_bot_self(self) -> None:
        ch, client = make_mock_channel()
        received: list[InboundMessage] = []

        async def handler(msg: InboundMessage) -> None:
            received.append(msg)

        ch.set_inbound_handler(handler)

        mock_msg = MagicMock(spec=discord.Message)
        mock_msg.author = MagicMock()
        mock_msg.author.bot = True
        mock_msg.author.id = 999

        await ch._on_message(mock_msg)
        assert len(received) == 0
