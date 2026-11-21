"""Tests for DiscordChannel message operations: edit, delete, reactions, typing, channel resolution and history fetch."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytest.importorskip("discord")
import discord

from app.channels.core.exceptions import ChannelSendError
from app.channels.types import OutboundMessage


@pytest.mark.asyncio
async def test_edit_message(channel):
    mock_ch = MagicMock()
    mock_msg = MagicMock()
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_msg.edit = AsyncMock()
    channel._resolve_channel = AsyncMock(return_value=mock_ch)

    await channel.edit_message("123", "456", "updated text")

    mock_msg.edit.assert_called_once_with(content="updated text")


@pytest.mark.asyncio
async def test_edit_message_channel_not_found(channel):
    channel._resolve_channel = AsyncMock(return_value=None)
    with pytest.raises(ChannelSendError, match="not reachable"):
        await channel.edit_message("123", "456", "text")


@pytest.mark.asyncio
async def test_edit_placeholder_message(channel):
    mock_ch = MagicMock()
    mock_target = MagicMock()
    mock_ch.fetch_message = AsyncMock(return_value=mock_target)
    mock_target.edit = AsyncMock()
    channel._resolve_channel = AsyncMock(return_value=mock_ch)

    msg = OutboundMessage(
        channel="discord",
        recipient_id="123",
        content="final answer",
        user_id="u1",
        reasoning="because reasons",
    )
    await channel.edit_placeholder_message("123", "456", msg)

    mock_target.edit.assert_called_once()
    call_kwargs = mock_target.edit.call_args.kwargs
    assert call_kwargs["embed"].description == "final answer"


@pytest.mark.asyncio
async def test_delete_message(channel):
    mock_ch = MagicMock()
    mock_msg = MagicMock()
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_msg.delete = AsyncMock()
    channel._resolve_channel = AsyncMock(return_value=mock_ch)

    await channel.delete_message("123", "456")

    mock_msg.delete.assert_called_once()


@pytest.mark.asyncio
async def test_react_to_message(channel):
    mock_ch = MagicMock()
    mock_msg = MagicMock()
    mock_ch.fetch_message = AsyncMock(return_value=mock_msg)
    mock_msg.add_reaction = AsyncMock()
    channel._resolve_channel = AsyncMock(return_value=mock_ch)

    await channel.react_to_message("123", "456", "👍")

    mock_msg.add_reaction.assert_called_once_with("👍")


@pytest.mark.asyncio
async def test_react_to_message_empty_emoji(channel):
    """Empty emoji should no-op without calling resolve_channel."""
    channel._resolve_channel = AsyncMock()
    await channel.react_to_message("123", "456", "")
    channel._resolve_channel.assert_not_called()


@pytest.mark.asyncio
async def test_start_typing(channel):
    mock_ch = MagicMock()
    mock_ch.typing = AsyncMock()
    channel._resolve_channel = AsyncMock(return_value=mock_ch)

    await channel.start_typing("123")

    mock_ch.typing.assert_called_once()


@pytest.mark.asyncio
async def test_resolve_channel_cache_hit(channel):
    """_resolve_channel should return channel from client cache."""
    mock_ch = MagicMock(spec=discord.TextChannel)
    channel._client = MagicMock()
    channel._client.get_channel = MagicMock(return_value=mock_ch)

    result = await channel._resolve_channel("12345")

    assert result == mock_ch
    channel._client.get_channel.assert_called_once_with(12345)


@pytest.mark.asyncio
async def test_resolve_channel_no_client(channel):
    """_resolve_channel should return None when client is not set."""
    channel._client = None
    result = await channel._resolve_channel("12345")
    assert result is None


@pytest.mark.asyncio
async def test_resolve_channel_api_fallback(channel):
    """_resolve_channel should fall back to fetch_channel when get_channel returns None."""
    mock_ch = MagicMock(spec=discord.TextChannel)
    channel._client = MagicMock()
    channel._client.get_channel = MagicMock(return_value=None)
    channel._client.fetch_channel = AsyncMock(return_value=mock_ch)

    result = await channel._resolve_channel("12345")

    assert result == mock_ch
    channel._client.fetch_channel.assert_called_once_with(12345)


@pytest.mark.asyncio
async def test_discord_fetch_history(channel):
    """Test fetch_history on DiscordChannel with mock async iterator."""
    channel._bot_id = "bot-id"
    mock_channel = MagicMock(spec=discord.TextChannel)
    mock_channel.id = 12345
    mock_channel.topic = "test topic"

    # Configure mock author
    mock_author = MagicMock()
    mock_author.id = "user-abc"
    mock_author.display_name = "Alice"
    mock_author.bot = False

    # Configure mock messages
    msg1 = MagicMock(spec=discord.Message)
    msg1.id = 111
    msg1.author = mock_author
    msg1.content = "Hello there"
    msg1.created_at = MagicMock()
    msg1.created_at.timestamp.return_value = 1779340000.0
    msg1.attachments = []
    msg1.embeds = []
    msg1.guild = None

    # This is a bot message that should be skipped
    mock_bot_author = MagicMock()
    mock_bot_author.id = "bot-id"
    mock_bot_author.bot = True
    msg2 = MagicMock(spec=discord.Message)
    msg2.id = 222
    msg2.author = mock_bot_author
    msg2.content = "I am a bot"
    msg2.created_at = MagicMock()
    msg2.created_at.timestamp.return_value = 1779340010.0
    msg2.attachments = []
    msg2.embeds = []
    msg2.guild = None

    class MockAsyncIterator:
        def __init__(self, items):
            self.items = items
            self.index = 0

        def __aiter__(self):
            return self

        async def __anext__(self):
            if self.index >= len(self.items):
                raise StopAsyncIteration
            item = self.items[self.index]
            self.index += 1
            return item

    mock_channel.history.return_value = MockAsyncIterator([msg1, msg2])

    with patch.object(channel, "_resolve_channel", AsyncMock(return_value=mock_channel)):
        res = await channel.fetch_history("12345", limit=5)

        # msg2 should be filtered out because it is a bot message
        assert len(res) == 1
        assert res[0].content == "Hello there"
        assert res[0].sender_id == "user-abc"
        assert res[0].sender_name == "Alice"
        assert res[0].sent_at == 1779340000.0
