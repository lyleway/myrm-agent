"""Inbound media groups: the messages of one Telegram album are emitted as a single message."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.providers.telegram import TelegramChannel
from app.channels.types import ChannelStatus, InboundMessage
from tests.channels.providers.telegram.telegram_channel_factories import make_channel

_DEBOUNCE = "app.channels.providers.telegram.inbound._MEDIA_GROUP_DEBOUNCE"
_DEADLINE = "app.channels.providers.telegram.inbound._MEDIA_GROUP_DEADLINE"
_ALBUM_KEY = "42:mg_1"


def _running_channel() -> tuple[TelegramChannel, list[InboundMessage]]:
    """A running channel together with the list of messages it emits."""
    ch = make_channel()
    received: list[InboundMessage] = []

    async def handler(msg: InboundMessage) -> None:
        received.append(msg)

    ch.set_inbound_handler(handler)
    ch._status = ChannelStatus.RUNNING
    return ch, received


def _album_update(message_id: int, caption: str) -> dict[str, object]:
    """The update of one photo of the album ``mg_1`` sent in a private chat."""
    return {
        "update_id": message_id,
        "message": {
            "message_id": message_id,
            "from": {"id": 42},
            "chat": {"id": 42, "type": "private"},
            "photo": [{"file_id": f"p{message_id}"}],
            "caption": caption,
            "media_group_id": "mg_1",
        },
    }


async def _feed(ch: TelegramChannel, raw: dict[str, object]) -> None:
    """Parse ``raw`` and hand the message to the channel the way the polling loop does."""
    msg = ch._parse_update(raw)
    assert msg is not None
    await ch._buffer_or_emit(msg, raw)


async def _wait_for_flush(ch: TelegramChannel) -> None:
    """Wait until every pending media-group flush has finished."""
    await asyncio.gather(*list(ch._mg_tasks.values()))


class TestBufferOrEmit:
    @pytest.mark.asyncio
    async def test_non_media_group_emits_directly(self) -> None:
        ch, received = _running_channel()
        raw = {
            "update_id": 1,
            "message": {
                "message_id": 10,
                "from": {"id": 42},
                "chat": {"id": 42, "type": "private"},
                "text": "hello",
            },
        }

        await _feed(ch, raw)

        assert len(received) == 1

    @pytest.mark.asyncio
    async def test_an_update_that_cannot_be_read_is_emitted_as_it_is(self) -> None:
        ch, received = _running_channel()
        msg = ch._parse_update(_album_update(10, "unreadable"))
        assert msg is not None

        await ch._buffer_or_emit(msg, {"update_id": "not a number"})

        assert [m.content for m in received] == ["unreadable"]
        assert ch._mg_buffers == {}

    @pytest.mark.asyncio
    async def test_media_group_buffers(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_DEBOUNCE, 0.01)
        ch, received = _running_channel()

        await _feed(ch, _album_update(10, "photo 1"))

        assert _ALBUM_KEY in ch._mg_buffers
        assert received == []
        await _wait_for_flush(ch)
        assert [m.content for m in received] == ["photo 1"]


class TestMediaGroupFlush:
    @pytest.mark.asyncio
    async def test_messages_of_one_group_are_merged_once_the_group_goes_quiet(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_DEBOUNCE, 0.01)
        ch, received = _running_channel()

        await _feed(ch, _album_update(10, "first"))
        await _feed(ch, _album_update(11, "second"))
        await _wait_for_flush(ch)

        [merged] = received
        assert merged.content == "first\nsecond"
        assert len(merged.media) == 2
        assert merged.metadata["media_group_count"] == 2
        assert (ch._mg_buffers, ch._mg_tasks) == ({}, {})

    @pytest.mark.asyncio
    async def test_group_that_never_goes_quiet_is_emitted_at_its_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_DEBOUNCE, 0.05)
        monkeypatch.setattr(_DEADLINE, 0.1)
        ch, received = _running_channel()
        await _feed(ch, _album_update(10, "first"))
        buf = ch._mg_buffers[_ALBUM_KEY]

        async def keep_sending() -> None:
            while True:
                buf.event.set()
                await asyncio.sleep(0.01)

        sender = asyncio.create_task(keep_sending())
        try:
            await asyncio.wait_for(ch._mg_tasks[_ALBUM_KEY], timeout=2)
        finally:
            sender.cancel()

        assert [m.content for m in received] == ["first"]
        assert ch._mg_buffers == {}

    @pytest.mark.asyncio
    async def test_stopping_the_channel_emits_a_pending_group_exactly_once(self) -> None:
        ch, received = _running_channel()
        ch._client = MagicMock()
        ch._client.close = AsyncMock()
        ch._client.delete_my_commands = AsyncMock()
        ch._poll_task = None
        await _feed(ch, _album_update(10, "first"))
        await _feed(ch, _album_update(11, "second"))
        flush = ch._mg_tasks[_ALBUM_KEY]
        await asyncio.sleep(0)  # let the flush start waiting for the group to go quiet

        await ch.stop()
        await asyncio.gather(flush)  # the cancelled flush ends without emitting the group a second time

        assert [m.content for m in received] == ["first\nsecond"]
        assert (ch._mg_buffers, ch._mg_tasks) == ({}, {})
