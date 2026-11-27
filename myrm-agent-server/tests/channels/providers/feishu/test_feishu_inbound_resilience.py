"""The Feishu inbound mixin keeps delivering when the platform leaves out or breaks part of an event.

Each test sends one imperfect event through the real channel and reads what the router receives: an event that cannot
be read is dropped without an error, a send time that cannot be read falls back to the arrival time, and a sender name,
quoted message or attachment the platform does not provide is left out while the message itself is handed over. A number
typed in answer to an action prompt is read as more than it says: it becomes the action and addresses the bot.
"""

from __future__ import annotations

import json
import logging
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest

from app.channels.providers.feishu import FeishuChannel
from app.channels.providers.feishu.action_fallback import FallbackActionSessionRegistry
from app.channels.providers.feishu.api import FeishuClient
from app.channels.types import ActionButton, InboundMessage, MediaType

_MEMBER = "ou_member"


def _channel() -> tuple[FeishuChannel, AsyncMock, list[InboundMessage]]:
    """A channel whose bot is ``ou_bot``, with its Feishu client and the messages it hands to the router."""
    channel = FeishuChannel(app_id="test_app_id", app_secret="test_app_secret")
    client = AsyncMock(spec=FeishuClient)
    client.bot_open_id = "ou_bot"
    client.is_configured = True
    client._get_http.return_value = AsyncMock(spec=httpx.AsyncClient)
    client.get_user.return_value = None
    channel._client = client
    channel._user_resolver._api = client
    received: list[InboundMessage] = []

    async def collect(msg: InboundMessage) -> None:
        received.append(msg)

    channel.set_inbound_handler(collect)
    return channel, client, received


def _typed(*, text: str = "hello", chat_type: str = "p2p", create_time: str = "", **message: str) -> dict[str, object]:
    """The event of a typed message of ``ou_member``; ``message`` overrides fields of the message."""
    fields = {
        "message_id": "om_1",
        "message_type": "text",
        "chat_id": "oc_chat",
        "chat_type": chat_type,
        "content": json.dumps({"text": text}),
        **message,
    }
    return {
        "header": {"event_type": "im.message.receive_v1", "create_time": create_time},
        "event": {
            "sender": {"sender_id": {"open_id": _MEMBER}, "sender_type": "user"},
            "message": fields,
        },
    }


def _tap(data: str, *, create_time: str = "") -> dict[str, object]:
    """The event of ``ou_member`` tapping the quick reply ``data`` of a card."""
    return {
        "header": {"event_type": "card.action.trigger", "create_time": create_time},
        "event": {
            "operator": {"open_id": _MEMBER},
            "action": {"tag": "button", "value": {"type": "qr", "data": data}},
            "context": {"open_chat_id": "oc_chat", "open_message_id": "om_card"},
        },
    }


def _quoted(msg_type: str, **fields: object) -> dict[str, object]:
    """The quoted message the platform returns for ``om_parent``."""
    return {
        "msg_type": msg_type,
        "body": {"content": json.dumps({})},
        "sender": {"sender_id": {"open_id": "ou_parent"}},
        **fields,
    }


class TestEventsThatCannotBeRead:
    @pytest.mark.asyncio
    async def test_a_payload_that_is_not_an_event_is_dropped(self) -> None:
        channel, _, received = _channel()

        assert await channel.handle_webhook_event({"event": "not an object"}) is None
        assert received == []

    @pytest.mark.asyncio
    async def test_a_card_event_that_cannot_be_read_is_acknowledged_and_dropped(self) -> None:
        channel, _, received = _channel()
        event = {"header": {"event_type": "card.action.trigger"}, "event": {"operator": _MEMBER}}

        assert await channel.handle_webhook_event(event) == {"toast": {"type": "info", "content": ""}}
        assert received == []


class TestSendTime:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "event", [_typed(create_time="soon"), _tap("yes", create_time="soon")], ids=["typed message", "card tap"]
    )
    async def test_a_send_time_that_cannot_be_read_falls_back_to_the_arrival_time(self, event: dict[str, object]) -> None:
        channel, _, received = _channel()
        before = time.time()

        await channel.handle_webhook_event(event)

        [msg] = received
        assert before <= msg.sent_at <= time.time()


class TestNumberedReplies:
    @pytest.mark.asyncio
    async def test_a_number_typed_in_answer_to_an_action_prompt_is_that_action_and_addresses_the_bot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        registry = FallbackActionSessionRegistry()
        monkeypatch.setattr("app.channels.providers.feishu.action_fallback.default_action_fallback_registry", registry)
        registry.register_actions(
            "oc_chat",
            _MEMBER,
            "om_prompt",
            [
                ActionButton(label="Allow", action_id="approval:approve:req-1"),
                ActionButton(label="Deny", action_id="approval:deny:req-1"),
            ],
        )
        channel, _, received = _channel()

        await channel.handle_webhook_event(_typed(text="2", chat_type="group"))

        [msg] = received
        assert msg.content == "approval:deny:req-1"
        assert msg.mentioned is True
        assert msg.metadata["callback_type"] == "act"
        assert msg.metadata["action_id"] == "approval:deny:req-1"
        assert msg.metadata["fallback_resolved"] is True


class TestQuotedMessage:
    @pytest.mark.asyncio
    async def test_a_quoted_message_the_platform_cannot_return_is_left_out(self) -> None:
        channel, client, received = _channel()
        client.get_message.return_value = None

        await channel.handle_webhook_event(_typed(parent_id="om_parent"))

        [msg] = received
        assert msg.reply_to is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("msg_type", "media_type"),
        [("image", MediaType.IMAGE), ("file", MediaType.DOCUMENT), ("audio", MediaType.AUDIO), ("media", MediaType.VIDEO)],
    )
    async def test_a_quoted_attachment_is_named_by_its_kind(self, msg_type: str, media_type: MediaType) -> None:
        channel, client, received = _channel()
        client.get_message.return_value = _quoted(msg_type)

        await channel.handle_webhook_event(_typed(parent_id="om_parent"))

        [msg] = received
        assert msg.reply_to is not None
        assert msg.reply_to.content == f"[{msg_type}]"
        assert [attachment.media_type for attachment in msg.reply_to.media] == [media_type]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("create_time", "expected"),
        [("1700000000000", 1_700_000_000.0), (1_700_000_000_000, 1_700_000_000.0), ("soon", None)],
        ids=["milliseconds as text", "milliseconds as number", "unreadable"],
    )
    async def test_the_quoted_message_carries_its_send_time_when_it_can_be_read(
        self, create_time: str | int, expected: float | None
    ) -> None:
        channel, client, received = _channel()
        client.get_message.return_value = _quoted("text", create_time=create_time)

        await channel.handle_webhook_event(_typed(parent_id="om_parent"))

        [msg] = received
        assert msg.reply_to is not None
        assert (msg.reply_to.sender_id, msg.reply_to.timestamp) == ("ou_parent", expected)


class TestSenderNames:
    @pytest.mark.asyncio
    async def test_a_message_is_delivered_without_a_name_when_the_name_cannot_be_resolved(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        channel, _, received = _channel()
        monkeypatch.setattr(channel._user_resolver, "resolve_user", AsyncMock(side_effect=RuntimeError("contact API down")))

        await channel.handle_webhook_event(_typed())

        [msg] = received
        assert (msg.content, msg.sender_name) == ("hello", None)


class TestAttachments:
    _FILE_MESSAGE = _typed(message_type="file", content=json.dumps({"file_key": "fk_1", "file_name": "report.pdf"}))

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "download",
        [{"return_value": b""}, {"side_effect": RuntimeError("download refused")}],
        ids=["empty", "refused"],
    )
    async def test_a_file_the_platform_does_not_deliver_is_left_out(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, download: dict[str, object]
    ) -> None:
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        channel, client, received = _channel()
        client.download_message_resource.configure_mock(**download)

        await channel.handle_webhook_event(self._FILE_MESSAGE)

        [msg] = received
        assert (msg.content, msg.media) == ("[file]", ())
        assert list((tmp_path / "feishu_media").iterdir()) == []

    @pytest.mark.asyncio
    async def test_a_refused_download_is_logged_with_the_file_key(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
        channel, client, _ = _channel()
        client.download_message_resource.side_effect = RuntimeError("download refused")

        with caplog.at_level(logging.WARNING, logger="app.channels.providers.feishu.inbound"):
            await channel.handle_webhook_event(self._FILE_MESSAGE)

        assert "Feishu media download failed: fk_1" in caplog.text
