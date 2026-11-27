"""A button press as Telegram's parser delivers it: the press never says group, the request the chat waits on does.

``TelegramChannel`` reports every ``callback_query`` as a message of a direct chat, wherever the button was pressed.
These tests hand the router's handlers what the real parser builds from the update, with the request opened in the
chat the update names: the approval card's own buttons (``act:approval:...``) and the answer buttons of the in-turn
approval card (``qr:/approve``).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.routing.router_keys import routing_state_key
from app.channels.types import InboundMessage
from tests.channels.providers.telegram.telegram_channel_factories import make_channel
from tests.channels.routing.action_button.testkit import ask, make_host, seed, status_of

# The chat the update names, the member whose message started the request, and another member of that chat.
_CHAT_ID = "-1001234567"
_CHAT = routing_state_key("telegram", _CHAT_ID)
_ASKER, _BYSTANDER = "101", "202"


def _pressed_by(sender_id: str, callback_data: str, chat_type: str) -> InboundMessage:
    """What ``TelegramChannel`` builds when ``sender_id`` presses the button carrying ``callback_data`` in the chat."""
    channel = make_channel()
    channel._client = MagicMock()
    channel._client.answer_callback_query = AsyncMock()
    channel._background_tasks = set()
    msg = channel._parse_update(
        {
            "update_id": 1,
            "callback_query": {
                "id": "cbq-1",
                "from": {"id": int(sender_id)},
                "data": callback_data,
                "message": {"message_id": 5, "chat": {"id": int(_CHAT_ID), "type": chat_type}},
            },
        }
    )
    assert msg is not None
    return msg


class TestApprovalCardButton:
    @pytest.mark.asyncio
    async def test_a_group_bystander_cannot_press_the_askers_button(self):
        host = make_host()
        ask(host, _ASKER, chat_id=_CHAT_ID)
        record_id = await seed("appr-tg-1")

        await host._handle_action_button_approval(_pressed_by(_BYSTANDER, f"act:approval:approve:{record_id}", "supergroup"))

        host._gate.submit.assert_not_called()
        assert _CHAT in host._pending_approvals, "the request stays open for the member who asked"
        assert await status_of(record_id) == "PENDING"

    @pytest.mark.asyncio
    async def test_the_member_who_asked_presses_it(self):
        host = make_host()
        ask(host, _ASKER, chat_id=_CHAT_ID)
        record_id = await seed("appr-tg-2")

        await host._handle_action_button_approval(_pressed_by(_ASKER, f"act:approval:approve:{record_id}", "supergroup"))

        host._gate.submit.assert_called_once()
        assert host._gate.submit.call_args.args[0].sender_id == _ASKER
        assert _CHAT not in host._pending_approvals
        assert await status_of(record_id) == "APPROVED"

    @pytest.mark.asyncio
    async def test_a_private_chat_press_is_decided_by_whoever_writes_there(self):
        host = make_host()
        ask(host, _ASKER, is_group=False, chat_id=_CHAT_ID)
        record_id = await seed("appr-tg-3")

        await host._handle_action_button_approval(_pressed_by(_ASKER, f"act:approval:approve:{record_id}", "private"))

        host._gate.submit.assert_called_once()
        assert await status_of(record_id) == "APPROVED"


class TestAnswerButton:
    @pytest.mark.asyncio
    async def test_a_group_bystander_cannot_press_the_cards_answer_button(self):
        host = make_host()
        ask(host, _ASKER, chat_id=_CHAT_ID)

        await host._handle_approval_command(_pressed_by(_BYSTANDER, "qr:/approve", "supergroup"), "allow_once")

        host._gate.submit.assert_not_called()
        assert _CHAT in host._pending_approvals, "the request stays open for the member who asked"
        host._bus.edit_channel_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_member_who_asked_presses_it(self):
        host = make_host()
        ask(host, _ASKER, chat_id=_CHAT_ID)

        await host._handle_approval_command(_pressed_by(_ASKER, "qr:/approve", "supergroup"), "allow_once")

        host._gate.submit.assert_called_once()
        assert host._gate.submit.call_args.args[0].sender_id == _ASKER
        assert _CHAT not in host._pending_approvals
