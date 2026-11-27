"""A button press as Telegram's parser delivers it: the press carries the kind of the chat it happened in.

These tests hand the router's handlers what the real parser builds from the update, with the request opened in the
chat the update names: the approval card's own buttons (``act:approval:...``) and the answer buttons of the in-turn
approval card (``qr:/approve``). A request is opened by a typed message or by a press, and in a group only the member
who started it answers it either way.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.routing.pending_approvals import PendingApproval
from app.channels.routing.router_keys import routing_state_key
from app.channels.types import InboundMessage
from app.core.channel_bridge.agent_executor.session import build_channel_session_key
from tests.channels.providers.telegram.telegram_channel_factories import make_channel
from tests.channels.routing.action_button.testkit import Host, ask, make_host, seed, status_of
from tests.support.pending_approvals import finished_turn

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


def _ask_by_press(host: Host, asker: str) -> None:
    """Open the request of the turn that ``asker``'s own tap on a quick reply of the group started."""
    started = _pressed_by(asker, "qr:build it", "supergroup")
    host._pending_approvals.register(_CHAT, PendingApproval(card_message_id="card-1", message=started, user_id=asker), None)
    host._active_tasks[_CHAT] = finished_turn(asker, chat_id=_CHAT_ID)


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


class TestRequestStartedByAPress:
    """The request carries the flag of the tap that started it, so a tap has to report its chat's kind itself."""

    @pytest.mark.asyncio
    async def test_a_group_bystander_cannot_press_the_cards_button(self):
        host = make_host()
        _ask_by_press(host, _ASKER)
        record_id = await seed("appr-tg-4")

        await host._handle_action_button_approval(_pressed_by(_BYSTANDER, f"act:approval:approve:{record_id}", "supergroup"))

        host._gate.submit.assert_not_called()
        assert _CHAT in host._pending_approvals, "the request stays open for the member who asked"
        assert await status_of(record_id) == "PENDING"

    @pytest.mark.asyncio
    async def test_a_group_bystander_cannot_press_the_cards_answer_button(self):
        host = make_host()
        _ask_by_press(host, _ASKER)

        await host._handle_approval_command(_pressed_by(_BYSTANDER, "qr:/approve", "supergroup"), "allow_once")

        host._gate.submit.assert_not_called()
        assert _CHAT in host._pending_approvals, "the request stays open for the member who asked"

    @pytest.mark.asyncio
    async def test_the_member_who_started_it_presses_the_answer_button(self):
        host = make_host()
        _ask_by_press(host, _ASKER)

        await host._handle_approval_command(_pressed_by(_ASKER, "qr:/approve", "supergroup"), "allow_once")

        host._gate.submit.assert_called_once()
        assert host._gate.submit.call_args.args[0].sender_id == _ASKER
        assert _CHAT not in host._pending_approvals


class TestPressJoinsTheGroupConversation:
    @pytest.mark.asyncio
    async def test_a_press_continues_the_conversation_of_the_group_it_happened_in(self):
        typed = make_channel()._parse_update(
            {
                "update_id": 2,
                "message": {
                    "message_id": 6,
                    "from": {"id": int(_BYSTANDER)},
                    "chat": {"id": int(_CHAT_ID), "type": "supergroup"},
                    "text": "hello",
                },
            }
        )
        assert typed is not None

        assert build_channel_session_key(_pressed_by(_ASKER, "qr:build it", "supergroup")) == build_channel_session_key(typed)
