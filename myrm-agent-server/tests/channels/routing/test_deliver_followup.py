"""A reply that no message just asked for is delivered the way the answer to a message is; a conversation is busy while its turn runs.

The real router delivers; the bus, the topic store, the web hand-off and the data-plane ledger are recorders.
"""

from __future__ import annotations

import asyncio
import dataclasses
import tempfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.routing.router import AgentRouter
from app.channels.routing.session_gate import SessionGateConfig
from app.channels.types import (
    METADATA_STORED_CHAT_KEY,
    InboundMessage,
    MediaAttachment,
    MediaType,
    OutboundMessage,
    ReplyMode,
    TopicContext,
)


def _topics(topic: TopicContext | None) -> MagicMock:
    """A topic store that resolves one topic context for any chat (the chat-level binding)."""
    return MagicMock(resolve_topic=AsyncMock(return_value=topic), sync_topic_metadata=AsyncMock())


class _Delivery:
    """The router under test with everything outside delivery recorded."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, topic: TopicContext | None) -> None:
        self.bus = MagicMock(publish_outbound=AsyncMock())
        self.router = AgentRouter(
            bus=self.bus,
            pairing_store=MagicMock(),
            agent_executor=MagicMock(),
            topic_resolver=_topics(topic),
        )
        self.handed_off: list[tuple[OutboundMessage, InboundMessage]] = []
        self.draft = AsyncMock()

        async def with_handoff(result: OutboundMessage, msg: InboundMessage) -> OutboundMessage:
            self.handed_off.append((result, msg))
            return result

        monkeypatch.setattr(self.router, "_attach_web_handoff", with_handoff)
        monkeypatch.setattr(self.router, "_create_outbound_draft", self.draft)
        monkeypatch.setattr("app.channels.routing.channel_data_plane.ChannelDataPlaneService.record_outbound", AsyncMock())

    @property
    def sent(self) -> list[OutboundMessage]:
        return [call.args[0] for call in self.bus.publish_outbound.await_args_list]


_STORED_CHAT = "chat-db-1"


def _origin(*, chat_id: str | None = "chat-1", thread_id: str | None = None) -> InboundMessage:
    return InboundMessage(
        channel="telegram",
        sender_id="u1",
        content="run the research in the background",
        chat_id=chat_id,
        thread_id=thread_id,
        user_id="owner",
        metadata={"locale": "zh-CN"},
    )


@pytest.fixture
def delivery(monkeypatch: pytest.MonkeyPatch) -> _Delivery:
    return _Delivery(monkeypatch, topic=None)


@pytest.mark.asyncio
async def test_the_reply_goes_to_the_chat_and_thread_the_conversation_lives_in(delivery: _Delivery) -> None:
    origin = _origin(thread_id="topic-7")

    assert await delivery.router.deliver_followup(origin, "The research is done.", stored_chat_id=_STORED_CHAT) is True

    (sent,) = delivery.sent
    assert (sent.channel, sent.recipient_id, sent.thread_id, sent.user_id) == ("telegram", "chat-1", "topic-7", "owner")
    assert sent.content == "The research is done."
    assert sent.metadata is not None and sent.metadata["locale"] == "zh-CN"


@pytest.mark.asyncio
async def test_a_direct_message_without_a_chat_id_is_answered_to_the_sender(delivery: _Delivery) -> None:
    assert await delivery.router.deliver_followup(_origin(chat_id=None), "done", stored_chat_id=_STORED_CHAT) is True

    assert [sent.recipient_id for sent in delivery.sent] == ["u1"]


@pytest.mark.asyncio
async def test_the_reply_carries_the_web_hand_off_of_the_chat_it_names_like_any_reply(delivery: _Delivery) -> None:
    origin = _origin()

    await delivery.router.deliver_followup(origin, "done", stored_chat_id=_STORED_CHAT)

    ((reply, handed_off_for),) = delivery.handed_off
    assert handed_off_for == origin
    assert reply.metadata is not None
    assert reply.metadata[METADATA_STORED_CHAT_KEY] == _STORED_CHAT


@pytest.mark.asyncio
async def test_the_apps_own_chat_gets_no_web_hand_off(delivery: _Delivery) -> None:
    """The web UI already is the conversation: the button only belongs to replies that go out over an IM channel."""
    origin = dataclasses.replace(_origin(), channel="web")

    assert await delivery.router.deliver_followup(origin, "done", stored_chat_id=_STORED_CHAT) is True

    assert delivery.handed_off == []


@pytest.mark.asyncio
async def test_a_silent_reply_is_not_sent(delivery: _Delivery) -> None:
    assert await delivery.router.deliver_followup(_origin(), "[SILENT]", stored_chat_id=_STORED_CHAT) is False

    assert delivery.sent == []


@pytest.mark.asyncio
async def test_a_topic_that_was_disabled_since_gets_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    delivery = _Delivery(monkeypatch, TopicContext(topic_id="chat-1", enabled=False))

    assert await delivery.router.deliver_followup(_origin(), "done", stored_chat_id=_STORED_CHAT) is False

    assert delivery.sent == []
    delivery.draft.assert_not_awaited()


def _temp_file() -> Path:
    """A file in the system temp directory, where the router may delete ephemeral attachments."""
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as handle:
        handle.write(b"image")
    return Path(handle.name)


def _attachment(path: Path, *, ephemeral: bool) -> MediaAttachment:
    return MediaAttachment(media_type=MediaType.IMAGE, path=str(path), filename=path.name, ephemeral=ephemeral)


@pytest.mark.asyncio
async def test_the_attachments_travel_with_the_reply(delivery: _Delivery) -> None:
    attached = _attachment(_temp_file(), ephemeral=False)
    try:
        assert (
            await delivery.router.deliver_followup(_origin(), "The chart is attached.", (attached,), stored_chat_id=_STORED_CHAT)
            is True
        )

        (sent,) = delivery.sent
        assert sent.media == (attached,)
    finally:
        Path(attached.path or "").unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_a_topic_that_was_disabled_since_leaves_no_temp_attachment_behind(monkeypatch: pytest.MonkeyPatch) -> None:
    delivery = _Delivery(monkeypatch, TopicContext(topic_id="chat-1", enabled=False))
    path = _temp_file()

    try:
        sent = await delivery.router.deliver_followup(
            _origin(), "done", (_attachment(path, ephemeral=True),), stored_chat_id=_STORED_CHAT
        )
        assert sent is False

        assert not path.exists()
    finally:
        path.unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_a_topic_that_holds_replies_for_review_gets_a_draft_and_the_reply_is_not_sent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    topic = TopicContext(topic_id="chat-1", reply_mode=ReplyMode.DRAFT_REVIEW)
    delivery = _Delivery(monkeypatch, topic)
    origin = _origin()

    assert await delivery.router.deliver_followup(origin, "Refund approved.", stored_chat_id=_STORED_CHAT) is False

    assert delivery.sent == []
    delivery.draft.assert_awaited_once()
    assert delivery.draft.await_args is not None
    held, held_for, held_chat, held_topic, placeholder = delivery.draft.await_args.args
    assert (held.content, held_for, held_chat, placeholder) == ("Refund approved.", origin, "chat-1", None)
    assert held_topic.reply_mode is ReplyMode.DRAFT_REVIEW


@pytest.mark.asyncio
async def test_a_reply_that_cannot_be_held_for_review_is_withheld_never_sent_unreviewed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    delivery = _Delivery(monkeypatch, TopicContext(topic_id="chat-1", reply_mode=ReplyMode.DRAFT_REVIEW))
    delivery.draft.side_effect = RuntimeError("approval store down")

    assert await delivery.router.deliver_followup(_origin(), "Refund approved.", stored_chat_id=_STORED_CHAT) is False

    assert delivery.sent == []


@pytest.mark.asyncio
async def test_a_conversation_is_busy_exactly_while_its_turn_runs() -> None:
    turn_may_end = asyncio.Event()

    async def turn(_msg: InboundMessage) -> None:
        await turn_may_end.wait()

    router = AgentRouter(
        bus=MagicMock(),
        pairing_store=MagicMock(),
        agent_executor=MagicMock(),
        session_gate_config=SessionGateConfig(debounce_window_ms=0),
    )
    router._gate._on_ready = turn
    origin = _origin()
    assert router.is_session_busy(origin) is False

    router._gate.submit(origin)

    assert router.is_session_busy(origin) is True
    assert router.is_session_busy(dataclasses.replace(origin, sender_id="u2")) is False
    turn_may_end.set()
    async with asyncio.timeout(5):
        while router.is_session_busy(origin):
            await asyncio.sleep(0.01)
