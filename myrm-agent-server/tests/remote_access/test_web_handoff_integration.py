"""Integration test: the browser button of an IM reply links the stored chat the reply was produced in.

The producer of a reply (the executor, or the router for a follow-up) names that chat in ``METADATA_STORED_CHAT_KEY``;
``_attach_web_handoff`` builds the button from it and never works out again which conversation a turn ran in. A turn's
conversation depends on inputs the delivery cannot see (the agent an alias routed it to, the session a resumed approval
belongs to), so every test stores the chat the turn ran in next to the chat the message itself would point at.

Chats go through the real ChatService over the real SQLite DB; the tunnel/ingress is monkey-patched.
"""

from __future__ import annotations

import pytest

from app.channels.routing.pipeline import RouterExecutionMixin
from app.channels.types import (
    METADATA_RESUME_SESSION_KEY,
    METADATA_STORED_CHAT_KEY,
    ActionButton,
    ButtonStyle,
    InboundMessage,
    OutboundMessage,
)
from app.channels.types.topics import METADATA_ROUTE_AGENT_KEY
from app.remote_access.tunnel_manager import TunnelManager, TunnelState
from app.services.chat.chat_service import ChatService
from app.services.event.app_event_bus import ServerEventBus


@pytest.fixture(autouse=True)
def _patch_tunnel_running(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = TunnelManager()
    manager._state = TunnelState.RUNNING
    manager._public_url = "https://tunnel-test.example.com"
    monkeypatch.setattr(
        "app.remote_access.tunnel_manager.get_tunnel_manager",
        lambda: manager,
    )

    async def _no_ingress() -> str:
        return ""

    monkeypatch.setattr(
        "app.core.infra.ingress.get_public_ingress_base_url",
        _no_ingress,
    )


async def _store_chat(session_key: str, channel: str = "telegram") -> str:
    """Store the channel chat of ``session_key``; return its database id."""
    return (await ChatService.get_or_create_channel_chat(session_key, channel)).id


def _reply(
    stored_chat_id: str | None, *, channel: str = "telegram", components: tuple[tuple[ActionButton, ...], ...] = ()
) -> OutboundMessage:
    """A reply the turn produced in the stored chat ``stored_chat_id``; ``None``: no stored chat produced it."""
    return OutboundMessage(
        channel=channel,
        recipient_id="peer",
        content="Hello from agent",
        user_id="local-user",
        metadata={METADATA_STORED_CHAT_KEY: stored_chat_id} if stored_chat_id else None,
        components=components,
    )


def _button_url(reply: OutboundMessage) -> str:
    """The URL of the browser button the router appended to ``reply``."""
    assert reply.components, "Expected handoff button to be attached"
    button = reply.components[-1][0]
    assert isinstance(button, ActionButton)
    assert button.action_id == "web:continue_chat"
    return button.url


async def test_the_button_links_the_database_chat_the_reply_was_produced_in() -> None:
    """The URL holds the chat's database id, not the IM peer id; the peer's other chat is not the one linked."""
    peer = "integration-test-peer-42"
    stored_id = await _store_chat("telegram:dm:somewhere-else")
    await _store_chat(f"telegram:dm:{peer}")

    patched = await RouterExecutionMixin._attach_web_handoff(
        _reply(stored_id), InboundMessage(channel="telegram", sender_id=peer, content="hi")
    )

    assert stored_id in _button_url(patched)
    assert peer not in _button_url(patched)


async def test_a_turn_routed_to_another_agent_links_the_chat_it_ran_in() -> None:
    """``/claude do this`` runs the turn under another agent in a chat of its own, not in the peer's default chat."""
    peer = "alias-peer"
    default_id = await _store_chat(f"telegram:dm:{peer}")
    routed_id = await _store_chat(f"telegram:dm:{peer}:agent:agent-x")
    routed = InboundMessage(channel="telegram", sender_id=peer, content="do this", metadata={METADATA_ROUTE_AGENT_KEY: "agent-x"})

    patched = await RouterExecutionMixin._attach_web_handoff(_reply(routed_id), routed)

    assert routed_id in _button_url(patched)
    assert default_id not in _button_url(patched)


async def test_a_resumed_turn_links_the_chat_it_continued() -> None:
    """An approval answered after the session rolled over continues the asking turn's chat, not the peer's current one."""
    peer = "resume-peer"
    asking_key = f"telegram:dm:{peer}:e=2020-01-01"
    asking_id = await _store_chat(asking_key)
    current_id = await _store_chat(f"telegram:dm:{peer}")
    answer = InboundMessage(
        channel="telegram", sender_id=peer, content="/approve", metadata={METADATA_RESUME_SESSION_KEY: asking_key}
    )

    patched = await RouterExecutionMixin._attach_web_handoff(_reply(asking_id), answer)

    assert asking_id in _button_url(patched)
    assert current_id not in _button_url(patched)


@pytest.mark.parametrize("metadata", [None, {"error_type": "budget_exhausted"}], ids=["no-metadata", "no-stored-chat"])
async def test_a_reply_no_stored_chat_produced_gets_no_button(metadata: dict[str, object] | None) -> None:
    """A reply that came before the turn had a stored chat (an FAQ answer, a spent budget) has no conversation to link."""
    peer = "no-chat-peer"
    await _store_chat(f"telegram:dm:{peer}")
    result = OutboundMessage(channel="telegram", recipient_id=peer, content="Hello", user_id="local-user", metadata=metadata)

    patched = await RouterExecutionMixin._attach_web_handoff(
        result, InboundMessage(channel="telegram", sender_id=peer, content="hi")
    )

    assert patched is result
    assert not patched.components


async def test_the_button_is_appended_to_the_components_the_reply_already_has() -> None:
    """Handoff button appends to, not replaces, existing components."""
    stored_id = await _store_chat("feishu:dm:preserve-test-peer", "feishu")
    approve = ActionButton(label="Approve", action_id="approval:approve:req-1", style=ButtonStyle.PRIMARY)

    patched = await RouterExecutionMixin._attach_web_handoff(
        _reply(stored_id, channel="feishu", components=((approve,),)),
        InboundMessage(channel="feishu", sender_id="preserve-test-peer", content="approve"),
    )

    assert len(patched.components) == 2
    assert patched.components[0][0].action_id == "approval:approve:req-1"
    assert patched.components[1][0].action_id == "web:continue_chat"


async def test_the_reply_is_still_delivered_when_the_button_cannot_be_built(monkeypatch: pytest.MonkeyPatch) -> None:
    """A failure while building the link must not block message delivery."""

    async def _exploding_button(_chat_id: str, *, locale: str) -> tuple[tuple[ActionButton, ...], ...]:
        raise RuntimeError("DB connection lost")

    monkeypatch.setattr("app.remote_access.mobile_deep_link.resolve_web_handoff_components", _exploding_button)
    result = _reply(await _store_chat("telegram:dm:crash-peer"))

    patched = await RouterExecutionMixin._attach_web_handoff(
        result, InboundMessage(channel="telegram", sender_id="crash-peer", content="hi")
    )

    assert patched is result
    assert not patched.components


@pytest.fixture
def sent_messages(monkeypatch: pytest.MonkeyPatch) -> list[OutboundMessage]:
    """The messages the channel bus is asked to send."""
    sent: list[OutboundMessage] = []

    async def _capture(msg: OutboundMessage) -> None:
        sent.append(msg)

    monkeypatch.setattr("app.core.channel_bridge.channel_gateway.bus.send_now", _capture)
    return sent


def _task_done_event(**overrides: str) -> dict[str, object]:
    """A finished-background-task event for an IM peer, with ``overrides`` applied."""
    return {
        "channel": "telegram",
        "chat_id": "btw-notify-peer-7",
        "task_id": "task-1",
        "status": "completed",
        "title": "Background task",
        "result": "done",
        "locale": "en",
        **overrides,
    }


async def test_btw_notifier_delivers_handoff_for_a_source_chat(sent_messages: list[OutboundMessage]) -> None:
    """``_deliver`` attaches the handoff button for the task's originating Chat UUID."""
    from app.core.channel_bridge.btw_notifier import BtwTaskNotifier

    chat = await ChatService.get_or_create_channel_chat("telegram:btw-notify-peer-7", "telegram")

    await BtwTaskNotifier(ServerEventBus())._deliver(_task_done_event(source_chat_id=chat.id))

    assert sent_messages, "the notification must still be delivered"
    components = sent_messages[-1].components or ()
    assert components, "the originating chat must get the handoff button"
    btn = components[-1][0]
    assert isinstance(btn, ActionButton)
    assert chat.id in btn.url
    assert "btw-notify-peer-7" not in btn.url


async def test_btw_notifier_btw_branch_skips_handoff(sent_messages: list[OutboundMessage]) -> None:
    """The BTW branch carries an IM peer in ``chat_id``, so there is no Chat row to link to."""
    from app.core.channel_bridge.btw_notifier import BtwTaskNotifier

    await BtwTaskNotifier(ServerEventBus())._deliver(
        _task_done_event(chat_id="im-peer-not-a-uuid", background_source="btw", task_id="task-2")
    )

    assert sent_messages, "the notification must still be delivered"
    assert not (sent_messages[-1].components or ()), "an IM peer must not be resolved as a Chat UUID"


async def test_btw_notifier_without_a_task_id_skips_handoff(sent_messages: list[OutboundMessage]) -> None:
    """A notification that names no task links no chat, even when it carries a source chat."""
    from app.core.channel_bridge.btw_notifier import BtwTaskNotifier

    chat = await ChatService.get_or_create_channel_chat("telegram:btw-no-task-peer", "telegram")

    await BtwTaskNotifier(ServerEventBus())._deliver(_task_done_event(source_chat_id=chat.id, task_id=""))

    assert sent_messages, "the notification must still be delivered"
    assert not (sent_messages[-1].components or ())


async def test_idempotent_get_or_create() -> None:
    """Multiple calls with same session_key return same DB UUID."""
    session_key = "telegram:idempotent-peer"
    chat1 = await ChatService.get_or_create_channel_chat(session_key, "telegram")
    chat2 = await ChatService.get_or_create_channel_chat(session_key, "telegram")
    assert chat1.id == chat2.id
