"""A Feishu card click carries the kind of the chat it happened in, taken from what the chat's typed messages reported.

The card callback names its chat (``open_chat_id``) but not whether it is a group, so the channel remembers the group
chats its typed messages came from. A chat the memory does not hold (never seen, or evicted) is reported as a direct chat.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock

import httpx
import pytest

from app.channels.providers.feishu import FeishuChannel
from app.channels.providers.feishu.api import FeishuClient
from app.channels.providers.feishu.group_chats import GroupChatMemory
from app.channels.routing.pending_approvals import PendingApproval
from app.channels.routing.router_keys import routing_state_key
from app.channels.types import InboundMessage
from tests.channels.routing.action_button.testkit import Host, make_host
from tests.support.pending_approvals import finished_turn

_GROUP, _DIRECT = "oc_group", "oc_direct"
_ASKER, _BYSTANDER = "ou_asker", "ou_bystander"


def _channel() -> tuple[FeishuChannel, list[InboundMessage]]:
    """A channel whose bot is ``ou_bot``, with the messages it hands to the router collected."""
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
    return channel, received


def _typed(chat_id: str, chat_type: str) -> dict[str, object]:
    """The event of a typed message of ``ou_member`` in ``chat_id``."""
    return {
        "header": {"event_type": "im.message.receive_v1"},
        "event": {
            "sender": {"sender_id": {"open_id": "ou_member"}, "sender_type": "user"},
            "message": {
                "message_id": f"om_{chat_id}",
                "message_type": "text",
                "chat_id": chat_id,
                "chat_type": chat_type,
                "content": json.dumps({"text": "hello"}),
            },
        },
    }


def _tap(chat_id: str, sender_id: str, data: str) -> dict[str, object]:
    """The event of ``sender_id`` tapping the quick reply ``data`` of a card in ``chat_id``."""
    return {
        "header": {"event_type": "card.action.trigger"},
        "event": {
            "operator": {"open_id": sender_id},
            "action": {"tag": "button", "value": {"type": "qr", "data": data}},
            "context": {"open_chat_id": chat_id, "open_message_id": "om_card"},
        },
    }


def _handed_over(received: list[InboundMessage], content: str) -> InboundMessage:
    """The message with ``content`` that the channel handed to the router."""
    [msg] = [m for m in received if m.content == content]
    return msg


class TestGroupChatMemory:
    def test_it_knows_the_chats_it_was_told_of(self):
        memory = GroupChatMemory()
        memory.remember(_GROUP)

        assert _GROUP in memory
        assert _DIRECT not in memory

    def test_an_empty_id_names_no_chat(self):
        memory = GroupChatMemory()
        memory.remember("")

        assert "" not in memory

    def test_the_longest_unseen_chat_is_forgotten_first(self):
        memory = GroupChatMemory(capacity=2)
        for chat_id in ("oc_a", "oc_b", "oc_a", "oc_c"):
            memory.remember(chat_id)

        assert "oc_b" not in memory
        assert "oc_a" in memory
        assert "oc_c" in memory


class TestCardClickChat:
    @pytest.mark.asyncio
    async def test_a_tap_in_a_group_a_typed_message_came_from_is_a_group_tap(self):
        channel, received = _channel()
        await channel.handle_webhook_event(_typed(_GROUP, "group"))

        await channel.handle_webhook_event(_tap(_GROUP, _ASKER, "yes"))

        tap = _handed_over(received, "yes")
        assert (tap.chat_id, tap.is_group, tap.mentioned) == (_GROUP, True, True)

    @pytest.mark.asyncio
    async def test_a_tap_in_a_direct_chat_stays_a_direct_tap(self):
        channel, received = _channel()
        await channel.handle_webhook_event(_typed(_DIRECT, "p2p"))

        await channel.handle_webhook_event(_tap(_DIRECT, _ASKER, "yes"))

        tap = _handed_over(received, "yes")
        assert (tap.chat_id, tap.is_group, tap.mentioned) == (_DIRECT, False, False)

    @pytest.mark.asyncio
    async def test_a_tap_in_a_chat_no_message_came_from_is_a_direct_tap(self):
        channel, received = _channel()

        await channel.handle_webhook_event(_tap(_GROUP, _ASKER, "yes"))

        tap = _handed_over(received, "yes")
        assert (tap.is_group, tap.mentioned) == (False, False)


class TestRequestStartedByATap:
    """The request carries the flag of the tap that started it, so the tap has to report its chat's kind itself."""

    @staticmethod
    async def _answered_by(member: str) -> tuple[Host, str]:
        """The router after ``member`` taps ``/approve`` on the card of the request ``ou_asker``'s own tap started."""
        channel, received = _channel()
        await channel.handle_webhook_event(_typed(_GROUP, "group"))
        await channel.handle_webhook_event(_tap(_GROUP, _ASKER, "build it"))
        host = make_host()
        chat = routing_state_key("feishu", _GROUP)
        started = PendingApproval(card_message_id="om_card", message=_handed_over(received, "build it"), user_id=_ASKER)
        host._pending_approvals.register(chat, started, None)
        host._active_tasks[chat] = finished_turn(_ASKER, channel="feishu", chat_id=_GROUP)

        await channel.handle_webhook_event(_tap(_GROUP, member, "/approve"))
        await host._handle_approval_command(_handed_over(received, "/approve"), "allow_once")
        return host, chat

    @pytest.mark.asyncio
    async def test_a_group_bystander_cannot_press_the_answer_button(self):
        host, chat = await self._answered_by(_BYSTANDER)

        host._gate.submit.assert_not_called()
        assert chat in host._pending_approvals, "the request stays open for the member who asked"

    @pytest.mark.asyncio
    async def test_the_member_who_started_it_presses_the_answer_button(self):
        host, chat = await self._answered_by(_ASKER)

        host._gate.submit.assert_called_once()
        assert chat not in host._pending_approvals
