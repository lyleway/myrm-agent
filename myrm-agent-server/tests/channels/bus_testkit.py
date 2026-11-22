"""Shared fakes for the message-bus tests: a recording channel, edit-capable variants and an outbound-message factory."""

from __future__ import annotations

from app.channels.core.base import BaseChannel
from app.channels.types import ActionButton, ChannelCapabilities, OutboundMessage, QuickReply, SelectMenu


class FakeChannel(BaseChannel):
    name = "fake"
    capabilities = ChannelCapabilities(
        buttons=True,
        quick_replies=True,
        select_menus=True,
    )

    def __init__(self, *, name: str = "fake", caps: ChannelCapabilities | None = None) -> None:
        super().__init__()
        if name != "fake":
            type(self).name = name  # type: ignore[assignment]
        if caps:
            type(self).capabilities = caps  # type: ignore[assignment]
        self.sent: list[OutboundMessage] = []

    async def send(self, msg: OutboundMessage) -> str | None:
        self.sent.append(msg)
        return "msg_id_1"


def make_out(
    channel: str = "fake",
    content: str = "hello",
    components: tuple[tuple[ActionButton | SelectMenu, ...], ...] = (),
    quick_replies: tuple[QuickReply, ...] = (),
) -> OutboundMessage:
    return OutboundMessage(
        channel=channel,
        recipient_id="user1",
        content=content,
        user_id="test_user",
        components=components,
        quick_replies=quick_replies,
    )


class EditRecordingChannel(FakeChannel):
    """Declares its own capabilities so the shared FakeChannel class state cannot leak into it."""

    capabilities = ChannelCapabilities(edit=True)

    def __init__(self) -> None:
        super().__init__()
        self.edits: list[tuple[str, str, str]] = []

    async def edit_message(self, chat_id: str, message_id: str, text: str) -> None:
        self.edits.append((chat_id, message_id, text))


class NoEditCapabilityChannel(EditRecordingChannel):
    capabilities = ChannelCapabilities(edit=False)
