"""Tests for DiscordChannel sending: forum threads, regular channels, long-message splitting and reply targeting."""

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("discord")
import discord

from app.channels.core.exceptions import ChannelSendError
from app.channels.providers.discord.helpers import derive_thread_name, is_forum_channel, reply_reference
from app.channels.types import OutboundMessage


class TestIsForumChannel:
    def test_none_returns_false(self):
        assert is_forum_channel(None) is False

    def test_regular_channel_returns_false(self):
        ch = MagicMock(spec=discord.TextChannel)
        ch.type = MagicMock()
        ch.type.value = 0
        assert is_forum_channel(ch) is False

    def test_forum_channel_by_isinstance(self):
        ch = MagicMock(spec=discord.ForumChannel)
        assert is_forum_channel(ch) is True

    def test_forum_channel_by_type_value(self):
        ch = MagicMock()
        del ch.spec  # Not a ForumChannel instance
        ch.type = MagicMock()
        ch.type.value = 15
        assert is_forum_channel(ch) is True

    def test_no_type_attribute(self):
        ch = MagicMock(spec=[])
        assert is_forum_channel(ch) is False


class TestDeriveThreadName:
    def test_single_line(self):
        assert derive_thread_name("Hello world") == "Hello world"

    def test_multi_line(self):
        assert derive_thread_name("First\nSecond") == "First"

    def test_strips_markdown_heading(self):
        assert derive_thread_name("## My Title") == "My Title"

    def test_empty_content(self):
        assert derive_thread_name("") == "New Post"

    def test_whitespace_only(self):
        assert derive_thread_name("   \n  ") == "New Post"

    def test_hash_only(self):
        assert derive_thread_name("###") == "New Post"

    def test_skips_empty_leading_lines(self):
        assert derive_thread_name("\n\nActual Title\nBody") == "Actual Title"

    def test_skips_blank_leading_with_markdown(self):
        assert derive_thread_name("\n# Report Title\nContent") == "Report Title"

    def test_truncates_to_100(self):
        long = "A" * 200
        assert len(derive_thread_name(long)) == 100


def _setup_resolve(channel, mock_channel):
    """Patch _resolve_channel to directly return the mock, bypassing isinstance checks."""
    channel._resolve_channel = AsyncMock(return_value=mock_channel)


def _make_forum_mock(*, requires_tag: bool = False, available_tags: list[MagicMock] | None = None):
    mock_forum = MagicMock(spec=discord.ForumChannel)
    mock_forum.type = MagicMock()
    mock_forum.type.value = 15
    mock_forum.flags = discord.ChannelFlags(require_tag=requires_tag)
    mock_forum.available_tags = available_tags or []
    return mock_forum


@pytest.mark.asyncio
async def test_send_to_forum_channel(channel):
    """send() should auto-create a forum thread when target is a Forum channel."""
    mock_forum = _make_forum_mock()

    mock_thread = MagicMock()
    mock_thread.id = 99999
    starter_msg = MagicMock()
    starter_msg.id = 88888
    mock_thread.message = starter_msg
    mock_thread.send = AsyncMock()
    mock_forum.create_thread = AsyncMock(return_value=mock_thread)

    _setup_resolve(channel, mock_forum)

    msg = OutboundMessage(
        channel="discord",
        recipient_id="12345",
        content="Daily report\nSome content here",
        user_id="u1",
    )
    result = await channel.send(msg)

    mock_forum.create_thread.assert_called_once()
    call_kwargs = mock_forum.create_thread.call_args
    assert call_kwargs.kwargs["name"] == "Daily report"
    assert call_kwargs.kwargs["content"] == "Daily report\nSome content here"
    assert result == "88888"


@pytest.mark.asyncio
async def test_send_to_forum_with_require_tag(channel):
    """When Forum requires tags, the first available tag is applied."""
    mock_tag = MagicMock()
    mock_tag.id = 777
    mock_forum = _make_forum_mock(requires_tag=True, available_tags=[mock_tag])

    mock_thread = MagicMock()
    mock_thread.id = 99999
    mock_thread.message = MagicMock(id=88888)
    mock_thread.send = AsyncMock()
    mock_forum.create_thread = AsyncMock(return_value=mock_thread)

    _setup_resolve(channel, mock_forum)

    msg = OutboundMessage(
        channel="discord",
        recipient_id="12345",
        content="Tagged post",
        user_id="u1",
    )
    result = await channel.send(msg)

    call_kwargs = mock_forum.create_thread.call_args.kwargs
    assert call_kwargs["applied_tags"] == [mock_tag]
    assert result == "88888"


@pytest.mark.asyncio
async def test_send_to_regular_channel(channel):
    """send() should behave normally for non-Forum channels."""
    mock_ch = MagicMock(spec=discord.TextChannel)
    mock_ch.type = MagicMock()
    mock_ch.type.value = 0
    sent_msg = MagicMock()
    sent_msg.id = 55555
    mock_ch.send = AsyncMock(return_value=sent_msg)

    _setup_resolve(channel, mock_ch)

    msg = OutboundMessage(
        channel="discord",
        recipient_id="12345",
        content="Regular message",
        user_id="u1",
    )
    result = await channel.send(msg)

    mock_ch.send.assert_called_once_with(content="Regular message", files=discord.utils.MISSING)
    assert result == "55555"


@pytest.mark.asyncio
async def test_send_long_message_splits_via_render(channel):
    """Long Discord replies must use render() and send multiple messages."""
    from app.channels.rendering.renderer import render

    mock_ch = MagicMock(spec=discord.TextChannel)
    mock_ch.type = MagicMock()
    mock_ch.type.value = 0

    sent_counter = 0

    async def _send(**kwargs: object) -> MagicMock:
        nonlocal sent_counter
        sent_counter += 1
        msg = MagicMock()
        msg.id = 10000 + sent_counter
        return msg

    mock_ch.send = AsyncMock(side_effect=_send)
    _setup_resolve(channel, mock_ch)

    long_body = "Discord long reply line.\n" * 180
    msg = OutboundMessage(
        channel="discord",
        recipient_id="12345",
        content=long_body,
        user_id="u1",
    )
    expected_chunks = render(msg, channel.render_style)
    assert len(expected_chunks) >= 2

    result = await channel.send(msg)

    assert mock_ch.send.call_count == len(expected_chunks)
    for i, call in enumerate(mock_ch.send.call_args_list):
        assert call.kwargs["content"] == expected_chunks[i]
    assert result == str(10000 + mock_ch.send.call_count)


@pytest.mark.asyncio
async def test_send_reply_targets_original_message_on_first_chunk_only(channel):
    """A reply is a native Discord reply (message id + channel id) and only the first chunk carries it."""
    mock_ch = MagicMock(spec=discord.TextChannel)
    mock_ch.id = 12345
    mock_ch.type = MagicMock()
    mock_ch.type.value = 0
    mock_ch.send = AsyncMock(return_value=MagicMock(id=1))
    _setup_resolve(channel, mock_ch)

    msg = OutboundMessage(
        channel="discord",
        recipient_id="12345",
        content="Discord long reply line.\n" * 180,
        user_id="u1",
        reply_to_id="777",
    )
    await channel.send(msg)

    calls = mock_ch.send.call_args_list
    assert len(calls) >= 2
    reference = calls[0].kwargs["reference"]
    assert (reference.message_id, reference.channel_id, reference.fail_if_not_exists) == (777, 12345, False)
    assert all("reference" not in call.kwargs for call in calls[1:])


def test_reply_reference_requires_a_snowflake_message_and_a_channel_id():
    """Ids that Discord cannot address degrade to a plain post instead of raising."""
    channel_with_id = MagicMock(spec=discord.TextChannel)
    channel_with_id.id = 42
    channel_without_id = MagicMock(spec=discord.abc.Messageable)

    assert reply_reference(channel_with_id, "not-a-snowflake") is None
    assert reply_reference(channel_without_id, "777") is None


@pytest.mark.asyncio
async def test_send_placeholder_to_forum(channel):
    """send_placeholder() should create forum thread when target is Forum."""
    mock_forum = _make_forum_mock()

    mock_thread = MagicMock()
    mock_thread.id = 99999
    mock_thread.message = MagicMock(id=77777)
    mock_thread.send = AsyncMock()
    mock_forum.create_thread = AsyncMock(return_value=mock_thread)

    _setup_resolve(channel, mock_forum)

    result = await channel.send_placeholder("12345", "Thinking...")

    mock_forum.create_thread.assert_called_once()
    assert result == "77777"


@pytest.mark.asyncio
async def test_forum_thread_creation_failure(channel):
    """send() raises a permanent ChannelSendError when Discord refuses the forum thread."""
    response = MagicMock(status=403, reason="Forbidden")
    mock_forum = _make_forum_mock()
    mock_forum.id = 12345
    mock_forum.create_thread = AsyncMock(side_effect=discord.Forbidden(response, "Permission denied"))

    _setup_resolve(channel, mock_forum)

    msg = OutboundMessage(
        channel="discord",
        recipient_id="12345",
        content="Should fail",
        user_id="u1",
    )
    with pytest.raises(ChannelSendError) as excinfo:
        await channel.send(msg)
    assert (excinfo.value.status_code, excinfo.value.retriable) == (403, False)


@pytest.mark.asyncio
async def test_on_message_forum_thread_inherits_topic(channel):
    """Forum thread messages should have channel_topic from parent Forum."""
    mock_message = MagicMock()
    mock_message.author.bot = False
    mock_message.author.id = 123
    mock_message.author.display_name = "user"
    mock_message.guild = None
    mock_message.channel = MagicMock(spec=discord.Thread)
    mock_message.channel.id = 789
    mock_message.channel.topic = None
    parent = MagicMock()
    parent.topic = "Q&A Forum — keep answers concise"
    mock_message.channel.parent = parent
    mock_message.id = 107
    mock_message.content = "question"
    mock_message.attachments = []

    received = []

    async def handler(msg):
        received.append(msg)

    channel.set_inbound_handler(handler)
    await channel._on_message(mock_message)

    assert len(received) == 1
    assert received[0].metadata["channel_topic"] == "Q&A Forum — keep answers concise"


@pytest.mark.asyncio
async def test_on_message_regular_channel_no_topic(channel):
    """Regular text channel without topic should have None channel_topic."""
    mock_message = MagicMock()
    mock_message.author.bot = False
    mock_message.author.id = 123
    mock_message.author.display_name = "user"
    mock_message.guild = None
    mock_message.channel = MagicMock(spec=discord.TextChannel)
    mock_message.channel.id = 789
    mock_message.channel.topic = None
    mock_message.id = 108
    mock_message.content = "hello"
    mock_message.attachments = []

    received = []

    async def handler(msg):
        received.append(msg)

    channel.set_inbound_handler(handler)
    await channel._on_message(mock_message)

    assert len(received) == 1
    assert received[0].metadata["channel_topic"] is None


@pytest.mark.asyncio
async def test_on_interaction_forum_thread_topic(channel):
    """Interaction in a Forum thread should inherit parent topic in metadata."""
    mock_interaction = MagicMock()
    mock_interaction.type = discord.InteractionType.component
    mock_interaction.response = MagicMock()
    mock_interaction.response.defer = AsyncMock()
    mock_interaction.user.id = 123
    mock_interaction.user.display_name = "user"
    mock_interaction.channel_id = 789
    mock_interaction.guild_id = 456
    mock_interaction.id = 111
    mock_interaction.data = {"custom_id": "act:approve"}

    mock_interaction.channel = MagicMock(spec=discord.Thread)
    mock_interaction.channel.topic = None
    parent = MagicMock()
    parent.topic = "Support Forum — please be specific"
    mock_interaction.channel.parent = parent

    received = []

    async def handler(msg):
        received.append(msg)

    channel.set_inbound_handler(handler)
    await channel._on_interaction(mock_interaction)

    assert len(received) == 1
    assert received[0].metadata["channel_topic"] == "Support Forum — please be specific"
    assert received[0].content == "/action approve"
