"""The buttons of a reply survive the placeholder edit on Telegram, on every route the edit can take.

Nothing re-sends the buttons once the placeholder is replaced, so a route that forgets the inline keyboard leaves
a reply without its web hand-off button (or its approval buttons) that no later step restores.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.providers.telegram.api_core import TelegramApiError
from app.channels.types import ActionButton, OutboundMessage
from tests.channels.providers.telegram.telegram_channel_factories import make_channel

_PARSE_ERROR = TelegramApiError(400, "Bad Request: can't parse entities: unexpected end tag")
_LINK = "https://example.test/chat-1"
_KEYBOARD = {"inline_keyboard": [[{"text": "Continue in browser", "url": _LINK}]]}

# (the placeholder is a draft, Rich Messages are available, Telegram refuses the HTML once, the client method that sends the reply)
_ROUTES = [
    pytest.param(True, True, False, "send_rich_message", id="draft-rich"),
    pytest.param(True, False, False, "send_message", id="draft-html"),
    pytest.param(True, False, True, "send_message", id="draft-plain"),
    pytest.param(False, True, False, "edit_message_text", id="edit-rich"),
    pytest.param(False, False, False, "edit_message_text", id="edit-html"),
    pytest.param(False, False, True, "edit_message_text", id="edit-plain"),
]


def _reply_with_a_link_button() -> OutboundMessage:
    return OutboundMessage(
        channel="telegram",
        user_id="u1",
        recipient_id="42",
        content="Final answer",
        components=((ActionButton(label="Continue in browser", action_id="web:continue_chat", url=_LINK),),),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("is_draft", "rich_send", "html_refused", "method"), _ROUTES)
async def test_the_reply_keeps_its_buttons_on_every_route_of_the_edit(
    is_draft: bool, rich_send: bool, html_refused: bool, method: str
) -> None:
    channel = make_channel()
    channel._rich_send_available = rich_send
    channel._client = MagicMock()
    channel._client.send_message_draft = AsyncMock()
    sender = AsyncMock(side_effect=[_PARSE_ERROR, None] if html_refused else None)
    setattr(channel._client, method, sender)
    message_id = "draft:1" if is_draft else "100"
    if is_draft:
        channel._active_drafts[channel._draft_key("42", message_id)] = 1

    await channel.edit_placeholder_message("42", message_id, _reply_with_a_link_button())

    assert sender.await_count == (2 if html_refused else 1)
    sent = sender.await_args_list[-1].kwargs
    assert sent["reply_markup"] == _KEYBOARD
    if html_refused:
        assert sent["parse_mode"] == ""  # the repeat is plain text, and it keeps the buttons too
