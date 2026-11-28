"""Tests for BaseChannel message deduplication."""

import asyncio

import pytest

from app.channels.core.base import BaseChannel
from app.channels.core.inbound_pipeline import DedupMode
from app.channels.types import InboundMessage, OutboundMessage


class MockChannel(BaseChannel):
    """Minimal BaseChannel implementation for testing."""

    name = "mock"

    def __init__(self, **kwargs: object) -> None:
        super().__init__()
        for key, value in kwargs.items():
            setattr(self, f"_{key}", value)
        self.received_messages: list[InboundMessage] = []

    async def send(self, msg: OutboundMessage) -> str | None:
        return None

    async def _dispatch_inbound(self, msg: InboundMessage) -> None:
        self.received_messages.append(msg)


@pytest.fixture
def mock_channel() -> MockChannel:
    """Create a mock channel with LRU dedup mode."""
    channel = MockChannel()
    channel._dedup_mode = DedupMode.LRU
    channel._dedup_capacity = 3
    channel._inbound_handler = lambda msg: asyncio.sleep(0)
    return channel


@pytest.mark.asyncio
async def test_dedup_lru_basic(mock_channel: MockChannel) -> None:
    """Test LRU dedup: first message passes, duplicate is blocked."""
    msg1 = InboundMessage(
        channel="mock",
        sender_id="user1",
        content="hello",
        chat_id="chat1",
        message_id="msg1",
    )
    msg2 = InboundMessage(
        channel="mock",
        sender_id="user1",
        content="hello",
        chat_id="chat1",
        message_id="msg1",  # Same ID
    )

    await mock_channel._emit_inbound(msg1)
    await mock_channel._emit_inbound(msg2)

    assert len(mock_channel.received_messages) == 1
    assert mock_channel.received_messages[0].message_id == "msg1"


@pytest.mark.asyncio
async def test_dedup_lru_capacity(mock_channel: MockChannel) -> None:
    """Test LRU dedup: capacity limit eviction."""
    for i in range(5):
        msg = InboundMessage(
            channel="mock",
            sender_id="user1",
            content=f"msg{i}",
            chat_id="chat1",
            message_id=f"msg{i}",
        )
        await mock_channel._emit_inbound(msg)

    assert len(mock_channel._seen_msg_ids) == 3


@pytest.mark.asyncio
async def test_dedup_lru_eviction_order(mock_channel: MockChannel) -> None:
    """Test LRU dedup: oldest messages are evicted first."""
    for i in range(4):
        msg = InboundMessage(
            channel="mock",
            sender_id="user1",
            content=f"msg{i}",
            chat_id="chat1",
            message_id=f"msg{i}",
        )
        await mock_channel._emit_inbound(msg)

    assert "msg0" not in mock_channel._seen_msg_ids
    assert "msg1" in mock_channel._seen_msg_ids
    assert "msg2" in mock_channel._seen_msg_ids
    assert "msg3" in mock_channel._seen_msg_ids


@pytest.mark.asyncio
async def test_dedup_ttl_mode(mock_channel: MockChannel) -> None:
    """Test TTL dedup mode."""
    mock_channel._dedup_mode = DedupMode.TTL
    mock_channel._dedup_ttl = 0.1

    msg1 = InboundMessage(
        channel="mock",
        sender_id="user1",
        content="hello",
        chat_id="chat1",
        message_id="msg1",
    )
    await mock_channel._emit_inbound(msg1)
    assert "msg1" in mock_channel._seen_msg_ids

    await asyncio.sleep(0.15)

    msg2 = InboundMessage(
        channel="mock",
        sender_id="user1",
        content="hello",
        chat_id="chat1",
        message_id="msg2",
    )
    await mock_channel._emit_inbound(msg2)

    assert "msg1" not in mock_channel._seen_msg_ids
    assert "msg2" in mock_channel._seen_msg_ids


@pytest.mark.asyncio
async def test_dedup_disabled(mock_channel: MockChannel) -> None:
    """Test dedup disabled when _dedup_ttl = 0."""
    mock_channel._dedup_ttl = 0

    msg = InboundMessage(
        channel="mock",
        sender_id="user1",
        content="hello",
        chat_id="chat1",
        message_id="msg1",
    )
    await mock_channel._emit_inbound(msg)
    await mock_channel._emit_inbound(msg)

    assert len(mock_channel.received_messages) == 2


@pytest.mark.asyncio
async def test_dedup_no_message_id(mock_channel: MockChannel) -> None:
    """Test dedup: messages without message_id always pass through."""
    msg1 = InboundMessage(
        channel="mock",
        sender_id="user1",
        content="hello",
        chat_id="chat1",
    )
    msg2 = InboundMessage(
        channel="mock",
        sender_id="user1",
        content="hello",
        chat_id="chat1",
    )

    await mock_channel._emit_inbound(msg1)
    await mock_channel._emit_inbound(msg2)

    assert len(mock_channel.received_messages) == 2

    @pytest.mark.skip(reason="OpenTelemetry ProxyCounter cannot be read directly without SDK setup")
    @pytest.mark.asyncio
    async def test_dedup_metrics(mock_channel: MockChannel) -> None:
        """Test dedup metrics: hit/miss/eviction counters."""
        initial_hit = mock_channel._dedup_hit_counter._storage.get(("mock",), 0)
        initial_miss = mock_channel._dedup_miss_counter._storage.get(("mock",), 0)
        initial_eviction = mock_channel._dedup_eviction_counter._storage.get(("mock", "lru"), 0)

        msg1 = InboundMessage(
            channel="mock",
            sender_id="user1",
            content="hello",
            chat_id="chat1",
            message_id="msg1",
        )
        await mock_channel._emit_inbound(msg1)
        miss_count = mock_channel._dedup_miss_counter._storage.get(("mock",), 0)
        assert miss_count == initial_miss + 1

        await mock_channel._emit_inbound(msg1)
        hit_count = mock_channel._dedup_hit_counter._storage.get(("mock",), 0)
        assert hit_count == initial_hit + 1

        for i in range(4):
            msg = InboundMessage(
                channel="mock",
                sender_id="user1",
                content=f"msg{i}",
                chat_id="chat1",
                message_id=f"msg{i + 2}",
            )
            await mock_channel._emit_inbound(msg)

        eviction_count = mock_channel._dedup_eviction_counter._storage.get(("mock", "lru"), 0)
        assert eviction_count > initial_eviction


def _reaction(sender: str, emoji: str = "\U0001f44d") -> InboundMessage:
    """A reaction on the message ``card``: like the reaction events of every channel, it carries ``card`` as its id."""
    return InboundMessage(
        channel="mock",
        sender_id=sender,
        content=emoji,
        chat_id="chat1",
        is_group=True,
        mentioned=True,
        message_id="card",
        metadata={"reaction": True, "target_message_id": "card"},
    )


@pytest.mark.asyncio
async def test_dedup_reactions_of_different_members_on_one_message_are_not_repeats(mock_channel: MockChannel) -> None:
    await mock_channel._emit_inbound(_reaction("alice"))
    await mock_channel._emit_inbound(_reaction("bob"))

    assert [m.sender_id for m in mock_channel.received_messages] == ["alice", "bob"]


@pytest.mark.asyncio
async def test_dedup_reactions_of_one_member_with_different_emoji_are_not_repeats(mock_channel: MockChannel) -> None:
    await mock_channel._emit_inbound(_reaction("alice", "\U0001f525"))
    await mock_channel._emit_inbound(_reaction("alice"))

    assert [m.content for m in mock_channel.received_messages] == ["\U0001f525", "\U0001f44d"]


@pytest.mark.asyncio
async def test_dedup_a_redelivered_reaction_is_still_a_repeat(mock_channel: MockChannel) -> None:
    await mock_channel._emit_inbound(_reaction("alice"))
    await mock_channel._emit_inbound(_reaction("alice"))

    assert len(mock_channel.received_messages) == 1
