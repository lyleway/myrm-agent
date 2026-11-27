"""A link the system writes from a button reaches the user as it is; a link the reply cites is still checked.

Real here: the bus dispatch loop, the capability downgrade of a channel without buttons and the dead-link gate with its
network probe. The hosts end in ``.invalid``, which never resolves, so the probe of one reports a dead link without
any network: the gate would flag every link of this test if it probed them all.
"""

from __future__ import annotations

import asyncio

from app.channels.core.base import BaseChannel
from app.channels.core.bus import MessageBus
from app.channels.types import ActionButton, ChannelCapabilities, OutboundMessage, RenderStyle


class _TextOnlyChannel(BaseChannel):
    """A platform that has no buttons, so a link button reaches it as a line of text."""

    name = "text_only"
    capabilities = ChannelCapabilities()
    render_style = RenderStyle()

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[OutboundMessage] = []

    async def send(self, msg: OutboundMessage) -> str | None:
        self.sent.append(msg)
        return f"sent-{len(self.sent)}"


async def _delivered(message: OutboundMessage) -> OutboundMessage:
    """Publish ``message`` through a running bus and return what its only channel was asked to send."""
    bus = MessageBus()
    channel = _TextOnlyChannel()
    bus.register_channel(channel)
    await bus.start()
    try:
        await bus.publish_outbound(message)
        async with asyncio.timeout(15):
            while not channel.sent:
                await asyncio.sleep(0.05)
    finally:
        await bus.stop()
    return channel.sent[0]


def _reply(content: str, *buttons: ActionButton) -> OutboundMessage:
    return OutboundMessage(
        channel="text_only", recipient_id="peer-1", content=content, user_id="user-1", components=(tuple(buttons),)
    )


async def test_the_link_of_a_button_written_as_text_is_delivered_without_a_dead_link_note() -> None:
    link = "https://handoff.invalid/chat-1"
    delivered = await _delivered(_reply("Answer", ActionButton(label="Continue", action_id="web:continue_chat", url=link)))

    assert delivered.components == ()
    assert delivered.content == f"Answer\n\n• Continue → {link}"


async def test_a_dead_link_the_reply_cites_is_still_noted_beside_the_link_of_a_button() -> None:
    link = "https://handoff.invalid/chat-1"
    cited = "https://cited.invalid/doc"
    delivered = await _delivered(_reply(f"See {cited}", ActionButton(label="Continue", action_id="web:continue_chat", url=link)))

    note = delivered.content.split(f"• Continue → {link}", 1)[1]
    assert cited in note
    assert link not in note
