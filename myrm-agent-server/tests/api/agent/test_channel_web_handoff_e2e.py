"""Real-chain E2E: an IM turn run by a live model carries a button that opens the chat it was stored in.

Real here: the ``AgentRouter`` and ``MessageBus`` (session gate, delivery, web hand-off), the
``ChannelAgentExecutor`` with the model of ``.env.test``, the SQLite chat store behind ``ChatService``,
the model providers and the public address read from the ``UserConfig`` rows, and the chats API a
browser reads. Only the IM platform edge is a test channel that records what the platform would send.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import delete, select

from app.channels.core.base import BaseChannel
from app.channels.core.bus import MessageBus
from app.channels.protocols.pairing import DmPolicy, GroupPolicy, GroupTriggerMode, PairingRole, PairingStatus
from app.channels.routing.router import AgentRouter
from app.channels.routing.session_gate import SessionGateConfig
from app.channels.types import (
    ActionButton,
    ChannelCapabilities,
    ChannelStatus,
    InboundMessage,
    OutboundMessage,
    ReactionLevel,
    RenderStyle,
)
from app.core.channel_bridge.agent_executor import ChannelAgentExecutor
from app.core.channel_bridge.agent_executor.execute_preamble import preamble
from app.core.channel_bridge.config_cache import invalidate_user_configs_cache
from app.database.connection import get_session
from app.database.models import UserConfig
from app.services.chat.chat_service import ChatService
from tests.support.e2e_provider_seed import (
    infer_provider_id,
    resolve_e2e_llm_endpoints,
    strip_provider_prefix,
    upsert_provider,
)
from tests.support.minimal_app import build_minimal_app

logger = logging.getLogger(__name__)

_CHANNEL = "e2e_handoff"
_PUBLIC_BASE = "https://handoff-e2e.example.com"
_HANDOFF_ACTION = "web:continue_chat"
_OWNER = "e2e-owner"
_REPLY_TIMEOUT_SEC = 240.0
_IDLE_GRACE_SEC = 10.0
_SEEDED_KEYS = ("providers", "personalSettings")


class _RecordingChannel(BaseChannel):
    """The IM platform edge: it records every message the platform would be asked to send."""

    name = _CHANNEL
    capabilities = ChannelCapabilities(buttons=True)
    render_style = RenderStyle()

    def __init__(self) -> None:
        super().__init__()
        self._status = ChannelStatus.RUNNING
        self.sent: list[OutboundMessage] = []

    async def send(self, msg: OutboundMessage) -> str | None:
        self.sent.append(msg)
        return f"{_CHANNEL}-sent-{len(self.sent)}"


class _OwnerPairingStore:
    """Every sender is the paired owner."""

    async def resolve(self, channel: str, sender_id: str) -> str | None:
        return _OWNER

    async def touch_display_name(self, channel: str, sender_id: str, display_name: str) -> None:
        return None

    async def bind(self, channel: str, sender_id: str, user_id: str, **kwargs: object) -> None:
        return None

    async def unbind(self, channel: str, sender_id: str) -> None:
        return None

    async def get_status(self, channel: str, sender_id: str) -> PairingStatus | None:
        return PairingStatus.ACTIVE

    async def get_pairing_detail(self, channel: str, sender_id: str) -> tuple[PairingStatus, PairingRole, int | None] | None:
        return PairingStatus.ACTIVE, PairingRole.ADMIN, None


class _OpenPolicyProvider:
    """Direct messages are open and no reaction is sent."""

    async def get_dm_policy(self, channel: str) -> DmPolicy:
        return DmPolicy.OPEN

    async def get_group_policy(self, channel: str) -> GroupPolicy:
        return GroupPolicy.OPEN

    async def get_group_trigger(self, channel: str) -> tuple[GroupTriggerMode, list[str]]:
        return GroupTriggerMode.ALL, []

    async def get_reaction_level(self, channel: str) -> ReactionLevel:
        return ReactionLevel.OFF

    async def get_enabled_groups(self) -> set[str]:
        return set()

    async def get_guest_mode(self, channel: str) -> bool:
        return False

    async def get_free_response_chats(self, channel: str) -> set[str]:
        return set()

    async def get_default_user_id(self) -> str | None:
        return _OWNER


@dataclass(frozen=True)
class _Stack:
    router: AgentRouter
    channel: _RecordingChannel


def _config_rows() -> dict[str, dict[str, object]]:
    """The provider bundle of the ``.env.test`` model and the public address the hand-off link is built on."""
    endpoints = resolve_e2e_llm_endpoints()
    provider_id = infer_provider_id(endpoints.basic_model)
    primary = {"providerId": provider_id, "model": strip_provider_prefix(endpoints.basic_model)}
    providers = upsert_provider(
        [],
        provider_id=provider_id,
        model_id=primary["model"],
        api_url=endpoints.basic_base_url,
        api_key=endpoints.basic_api_key,
    )
    model_slot = {"primary": primary, "fallback": dict(primary), "temperature": 0.7}
    return {
        "providers": {
            "providers": providers,
            "defaultModelConfig": {"baseModel": model_slot, "liteModel": model_slot},
            "customModelInfo": {},
        },
        "personalSettings": {"publicIngressBaseUrl": _PUBLIC_BASE},
    }


@pytest.fixture
async def seeded_config() -> AsyncIterator[None]:
    """Store the model providers and the public address as the instance's own config rows, then restore the rows."""
    rows = _config_rows()
    async with get_session() as session:
        previous = (await session.execute(select(UserConfig).where(UserConfig.config_key.in_(_SEEDED_KEYS)))).scalars().all()
        saved = [
            UserConfig(
                id=row.id,
                config_key=row.config_key,
                config_value=row.config_value,
                version=row.version,
                last_device_id=row.last_device_id,
                is_encrypted=row.is_encrypted,
            )
            for row in previous
        ]
        await session.execute(delete(UserConfig).where(UserConfig.config_key.in_(_SEEDED_KEYS)))
        session.add_all(
            UserConfig(
                id=f"e2e-handoff-{key}",
                config_key=key,
                config_value=value,
                version="1_0",
                last_device_id="e2e",
                is_encrypted=False,
            )
            for key, value in rows.items()
        )
        await session.commit()
    invalidate_user_configs_cache()
    yield
    async with get_session() as session:
        await session.execute(delete(UserConfig).where(UserConfig.config_key.in_(_SEEDED_KEYS)))
        session.add_all(saved)
        await session.commit()
    invalidate_user_configs_cache()


@pytest.fixture
async def stack(seeded_config: None, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Stack]:
    """A running router and bus whose only channel is the recording platform edge, with the real executor.

    The agent runs without the web search tool: an agent that has it is only ready while a live search service answers
    a probe, and that service is an external dependency this hand-off chain does not touch.
    """
    monkeypatch.setattr(
        preamble,
        "DEFAULT_ENABLED_BUILTIN_TOOLS",
        tuple(tool for tool in preamble.DEFAULT_ENABLED_BUILTIN_TOOLS if tool != "web_search"),
    )
    bus = MessageBus()
    channel = _RecordingChannel()
    bus.register_channel(channel)
    router = AgentRouter(
        bus=bus,
        pairing_store=_OwnerPairingStore(),
        agent_executor=ChannelAgentExecutor(),
        policy_provider=_OpenPolicyProvider(),
        session_gate_config=SessionGateConfig(debounce_window_ms=0),
    )
    await bus.start()
    await router.start()
    yield _Stack(router=router, channel=channel)
    await router.stop()
    await bus.stop()


def _direct_message(sender: str, text: str) -> InboundMessage:
    return InboundMessage(
        channel=_CHANNEL,
        sender_id=sender,
        chat_id=sender,
        content=text,
        message_id=f"{_CHANNEL}-{uuid.uuid4().hex[:12]}",
    )


async def _reply_containing(stack: _Stack, origin: InboundMessage, marker: str, *, timeout_sec: float) -> OutboundMessage:
    """The first message the platform was asked to send that says ``marker``.

    A conversation that has gone idle after something was sent without the marker fails at once: that message is the
    turn's error reply, and waiting out the full timeout would only hide it.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_sec
    idle_since: float | None = None
    while loop.time() < deadline:
        for message in stack.channel.sent:
            if marker in message.content:
                return message
        if stack.channel.sent and not stack.router.is_session_busy(origin):
            idle_since = idle_since if idle_since is not None else loop.time()
            if loop.time() - idle_since > _IDLE_GRACE_SEC:
                break
        else:
            idle_since = None
        await asyncio.sleep(0.2)
    pytest.fail(f"nothing sent said {marker!r}; sent: {[m.content[:300] for m in stack.channel.sent]}")


def _handoff_chat_id(message: OutboundMessage) -> str:
    """The chat the message's single hand-off button opens, after checking the link is built on the public address."""
    buttons = [
        button
        for row in message.components
        for button in row
        if isinstance(button, ActionButton) and button.action_id == _HANDOFF_ACTION
    ]
    assert len(buttons) == 1, f"expected one {_HANDOFF_ACTION} button, got {message.components!r}"
    url = buttons[0].url or ""
    assert url.startswith(f"{_PUBLIC_BASE}/"), url
    chat_id = url.removeprefix(f"{_PUBLIC_BASE}/")
    assert chat_id and "/" not in chat_id, url
    logger.info("hand-off button under %r opens %s", message.content[:80], url)
    return chat_id


async def _stored_texts(chat_id: str) -> tuple[list[str], list[str]]:
    """(user texts, assistant texts) of the stored chat, oldest first."""
    messages = await ChatService.get_all_messages(chat_id)
    return (
        [m.content for m in messages if m.role == "user"],
        [m.content for m in messages if m.role == "assistant"],
    )


@pytest.fixture
async def opened_chat_ids() -> AsyncIterator[list[str]]:
    """Chat ids a test opened; they are hard-deleted afterwards so no row outlives the test."""
    ids: list[str] = []
    yield ids
    for chat_id in ids:
        await ChatService.permanently_delete_chat(chat_id)


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.timeout(600)
@pytest.mark.skipif(not os.environ.get("BASIC_API_KEY"), reason="E2E test requires BASIC_API_KEY environment variable")
async def test_a_model_run_im_turn_carries_a_button_that_opens_the_chat_it_was_stored_in(
    stack: _Stack, opened_chat_ids: list[str]
) -> None:
    sender = f"peer-{uuid.uuid4().hex[:8]}"
    first, second, followup_marker = (uuid.uuid4().hex[:8] for _ in range(3))

    # Turn 1: the live model answers; the reply carries the button of the chat the turn was stored in.
    first_turn = _direct_message(sender, f"Reply with exactly this single token and nothing else: HANDOFF-{first}")
    await stack.channel._dispatch_inbound(first_turn)
    first_reply = await _reply_containing(stack, first_turn, f"HANDOFF-{first}", timeout_sec=_REPLY_TIMEOUT_SEC)
    chat_id = _handoff_chat_id(first_reply)
    opened_chat_ids.append(chat_id)

    chat = await ChatService.get_chat_by_id(chat_id)
    assert chat is not None, "the button opens a chat that is not stored"
    assert chat.channel_session_key, "the opened chat is not the IM session's chat"
    user_texts, assistant_texts = await _stored_texts(chat_id)
    assert any(f"HANDOFF-{first}" in text for text in user_texts), user_texts
    assert any(f"HANDOFF-{first}" in text for text in assistant_texts), assistant_texts

    # Turn 2: the same sender continues the conversation, so the button opens the very same chat.
    follow_up_turn = _direct_message(sender, f"Reply with exactly this single token and nothing else: HANDOFF-{second}")
    await stack.channel._dispatch_inbound(follow_up_turn)
    second_reply = await _reply_containing(stack, follow_up_turn, f"HANDOFF-{second}", timeout_sec=_REPLY_TIMEOUT_SEC)
    assert _handoff_chat_id(second_reply) == chat_id
    user_texts, assistant_texts = await _stored_texts(chat_id)
    logger.info(
        "chat %s (session %s) holds %d user and %d assistant messages",
        chat_id,
        chat.channel_session_key,
        len(user_texts),
        len(assistant_texts),
    )
    assert any(f"HANDOFF-{first}" in text for text in assistant_texts)
    assert any(f"HANDOFF-{second}" in text for text in assistant_texts)

    # A message said later in that conversation without a user turn (a wake or background result) links the same chat.
    assert await stack.router.deliver_followup(follow_up_turn, f"FOLLOWUP-{followup_marker}", stored_chat_id=chat_id)
    followup = await _reply_containing(stack, follow_up_turn, f"FOLLOWUP-{followup_marker}", timeout_sec=30.0)
    assert _handoff_chat_id(followup) == chat_id

    # The page behind the link reads this chat through the chats API: the conversation is there to render.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=build_minimal_app(preset="chats")), base_url="http://testserver", timeout=60.0
    ) as client:
        landing = await client.get(f"/api/v1/chats/{chat_id}")
    assert landing.status_code == 200, landing.text
    body = json.dumps(landing.json(), ensure_ascii=False)
    assert f"HANDOFF-{first}" in body
    assert f"HANDOFF-{second}" in body
