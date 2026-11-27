"""A Teams placeholder becomes the final reply with the buttons of that reply: an edit of text alone would drop them.

The fake connector is the Bot Framework call that replaces an activity (``PUT /v3/conversations/{conversation}/activities/
{activity}``): it records the activity it is given, so what a person would see in Teams is what the test reads. The edit
goes through the router-side ``MessageEffects.edit_placeholder``, which hands the channel the reply to put in the
placeholder's place.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from app.channels.providers.msteams import MSTeamsChannel
from app.channels.providers.msteams.helpers import encode_message_key
from app.channels.rendering.renderer import render
from app.channels.routing.message_effects import MessageEffects
from app.channels.types import ActionButton, OutboundMessage, QuickReply

SERVICE_URL = "https://smba.example.net/emea"
CONVERSATION = "19:conv@thread.tacv2"
ACTIVITY = "1700000000000"
_LOGIN_HOST = "login.microsoftonline.com"
_ACTIVITY_PATH = f"/emea/v3/conversations/{CONVERSATION}/activities/{ACTIVITY}"
_ADAPTIVE_CARD = "application/vnd.microsoft.card.adaptive"
_HAND_OFF = ActionButton(label="Continue in browser", action_id="web:continue_chat", url="https://app.example.com/c1")


class _Connector:
    """Serves login, replaces and deletes the one activity of the conversation, and records both."""

    def __init__(self, *, refuses_replace: bool = False) -> None:
        self._refuses_replace = refuses_replace
        self.replaced: list[dict[str, object]] = []
        self.deleted = 0

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == _LOGIN_HOST:
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 3600})
        if request.url.path != _ACTIVITY_PATH or request.headers.get("authorization") != "Bearer tok":
            return httpx.Response(404)
        if request.method == "DELETE":
            self.deleted += 1
            return httpx.Response(204)
        if request.method != "PUT" or self._refuses_replace:
            return httpx.Response(403)
        self.replaced.append(json.loads(request.content))
        return httpx.Response(200, json={"id": ACTIVITY})


def _teams(connector: _Connector) -> MSTeamsChannel:
    channel = MSTeamsChannel(app_id="test_app_id", app_password="test_app_password")
    client = httpx.AsyncClient(transport=httpx.MockTransport(connector.handle))
    channel._http = client
    channel._api._http = client
    channel._api.cache_service_url(CONVERSATION, SERVICE_URL)
    return channel


def _bus(channel: MSTeamsChannel) -> MagicMock:
    async def _persist(msg: OutboundMessage) -> OutboundMessage:
        return msg

    bus = MagicMock()
    bus.get_channel = MagicMock(return_value=channel)
    bus.publish_outbound = AsyncMock()
    bus.durable_outbound.persist_direct_send = AsyncMock(side_effect=_persist)
    bus.durable_outbound.mark_attempting = AsyncMock()
    bus.durable_outbound.ack = AsyncMock()
    return bus


def _reply(content: str = "The answer.", **kwargs: object) -> OutboundMessage:
    return OutboundMessage(channel="teams", recipient_id=CONVERSATION, user_id="u1", content=content, **kwargs)


def _published(bus: MagicMock) -> list[OutboundMessage]:
    return [call.args[0] for call in bus.publish_outbound.await_args_list]


async def _edit_placeholder(channel: MSTeamsChannel, bus: MagicMock, reply: OutboundMessage) -> None:
    placeholder = encode_message_key(ACTIVITY, SERVICE_URL, CONVERSATION)
    await MessageEffects(bus).edit_placeholder("teams", CONVERSATION, placeholder, reply)


def _card(activity: dict[str, object]) -> dict[str, object]:
    (attachment,) = activity["attachments"]  # type: ignore[misc]
    assert attachment["contentType"] == _ADAPTIVE_CARD
    return attachment["content"]


class TestFinalReplyWithButtons:
    @pytest.mark.asyncio
    async def test_the_hand_off_button_is_in_the_card_that_replaces_the_placeholder(self) -> None:
        connector = _Connector()
        channel = _teams(connector)
        bus = _bus(channel)

        await _edit_placeholder(channel, bus, _reply(components=((_HAND_OFF,),)))

        (activity,) = connector.replaced
        assert "text" not in activity, "a text left in the activity would show beside the card"
        card = _card(activity)
        assert card["body"] == [{"type": "TextBlock", "text": "The answer.", "wrap": True}]
        assert card["actions"] == [
            {"type": "Action.OpenUrl", "title": "Continue in browser", "url": "https://app.example.com/c1"}
        ]
        bus.publish_outbound.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_channel_puts_quick_replies_in_the_card_too(self) -> None:
        connector = _Connector()
        channel = _teams(connector)
        placeholder = encode_message_key(ACTIVITY, SERVICE_URL, CONVERSATION)

        # Called on the channel itself: the router turns the quick replies of a channel that does not declare them into text.
        await channel.edit_placeholder_message(
            CONVERSATION, placeholder, _reply(quick_replies=(QuickReply(label="More", text="tell me more"),))
        )

        (activity,) = connector.replaced
        assert _card(activity)["actions"] == [{"type": "Action.Submit", "title": "More", "data": {"quick_reply": "tell me more"}}]

    @pytest.mark.asyncio
    async def test_a_long_reply_puts_its_first_chunk_in_the_card_and_sends_the_rest_as_messages(self) -> None:
        connector = _Connector()
        channel = _teams(connector)
        bus = _bus(channel)
        reply = _reply("Teams final answer line.\n" * 1200, components=((_HAND_OFF,),))
        chunks = render(reply, channel.render_style)
        assert len(chunks) >= 2

        await _edit_placeholder(channel, bus, reply)

        (activity,) = connector.replaced
        (block,) = _card(activity)["body"]
        assert block["text"] == chunks[0]
        assert len(block["text"]) <= channel.render_style.max_text_length
        assert [message.content for message in _published(bus)] == chunks[1:]
        assert all(not message.components for message in _published(bus)), "the button belongs to the first chunk only"


class TestFinalReplyWithoutButtons:
    @pytest.mark.asyncio
    async def test_the_placeholder_stays_a_text_edit_and_keeps_its_markdown(self) -> None:
        connector = _Connector()
        channel = _teams(connector)

        await _edit_placeholder(channel, _bus(channel), _reply("**bold** answer"))

        assert connector.replaced == [{"type": "message", "text": "**bold** answer"}]


class TestRefusedEdit:
    @pytest.mark.asyncio
    async def test_a_refused_edit_sends_the_reply_with_its_button_and_removes_the_placeholder(self) -> None:
        connector = _Connector(refuses_replace=True)
        channel = _teams(connector)
        bus = _bus(channel)

        await _edit_placeholder(channel, bus, _reply(components=((_HAND_OFF,),)))

        (sent,) = _published(bus)
        assert sent.components == ((_HAND_OFF,),)
        assert connector.deleted == 1
