"""The stack behind the web hand-off E2E tests: the real IM router and executor, and a platform edge that records.

Real here: the ``AgentRouter`` and ``MessageBus`` (session gate, placeholder, delivery, web hand-off), the
``ChannelAgentExecutor`` with the model of ``.env.test``, the SQLite chat store behind ``ChatService``, and the model
providers and public address read from the ``UserConfig`` rows. Only the IM platform edge is a test double: a channel
that records what the platform would be asked to send, and the placeholders it would be asked to edit.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from unittest import mock

import pytest
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
from app.database.models import Chat, UserConfig
from app.services.chat.chat_service import ChatService
from tests.support.e2e_provider_seed import (
    infer_provider_id,
    resolve_e2e_llm_endpoints,
    strip_provider_prefix,
    upsert_provider,
)

logger = logging.getLogger(__name__)

CHANNEL = "e2e_handoff"
PUBLIC_BASE = "https://handoff-e2e.example.com"
HANDOFF_ACTION = "web:continue_chat"
REPLY_TIMEOUT_SEC = 240.0
_OWNER = "e2e-owner"
_IDLE_GRACE_SEC = 10.0
_SEEDED_KEYS = ("providers", "personalSettings")


class RecordingChannel(BaseChannel):
    """The IM platform edge: records the messages the platform is asked to send and the placeholders it is asked to edit.

    A channel that declares ``edit`` takes a placeholder first, and the final reply then arrives as the edit of it
    (``edits``), as it does on a platform whose edit carries the whole message; any other reply is a ``sent`` message.
    """

    name = CHANNEL
    render_style = RenderStyle()

    def __init__(self, capabilities: ChannelCapabilities) -> None:
        super().__init__()
        self.capabilities = capabilities
        self._status = ChannelStatus.RUNNING
        self.said: list[OutboundMessage] = []  # what a person in the chat gets, in order: messages and final edits
        self.sent: list[OutboundMessage] = []
        self.edits: list[OutboundMessage] = []
        self.placeholders: list[str] = []

    async def send(self, msg: OutboundMessage) -> str | None:
        self.said.append(msg)
        self.sent.append(msg)
        return f"{CHANNEL}-sent-{len(self.sent)}"

    async def send_placeholder(self, chat_id: str, text: str, *, thread_id: str | None = None) -> str | None:
        if not self.capabilities.edit:
            return None
        self.placeholders.append(text)
        return f"{CHANNEL}-placeholder-{len(self.placeholders)}"

    async def edit_placeholder_message(self, chat_id: str, message_id: str, msg: OutboundMessage) -> None:
        self.said.append(msg)
        self.edits.append(msg)


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
    """Direct messages are open, and the chats in ``groups`` answer to every message."""

    def __init__(self, reaction_level: ReactionLevel, groups: frozenset[str]) -> None:
        self._reaction_level = reaction_level
        self._groups = groups

    async def get_dm_policy(self, channel: str) -> DmPolicy:
        return DmPolicy.OPEN

    async def get_group_policy(self, channel: str) -> GroupPolicy:
        return GroupPolicy.OPEN

    async def get_group_trigger(self, channel: str) -> tuple[GroupTriggerMode, list[str]]:
        return GroupTriggerMode.ALL, []

    async def get_reaction_level(self, channel: str) -> ReactionLevel:
        return self._reaction_level

    async def get_enabled_groups(self) -> set[str]:
        return set(self._groups)

    async def get_guest_mode(self, channel: str) -> bool:
        return False

    async def get_free_response_chats(self, channel: str) -> set[str]:
        return set()

    async def get_default_user_id(self) -> str | None:
        return _OWNER


@dataclass(frozen=True)
class Stack:
    router: AgentRouter
    channel: RecordingChannel


def _config_rows(public_base: str | None, model_id: str | None, *, with_model: bool) -> dict[str, dict[str, object]]:
    """The provider bundle of the ``.env.test`` model, and the public address the hand-off link is built on."""
    rows: dict[str, dict[str, object]] = {}
    if with_model:
        endpoints = resolve_e2e_llm_endpoints()
        provider_id = infer_provider_id(endpoints.basic_model)
        primary = {"providerId": provider_id, "model": model_id or strip_provider_prefix(endpoints.basic_model)}
        providers = upsert_provider(
            [],
            provider_id=provider_id,
            model_id=primary["model"],
            api_url=endpoints.basic_base_url,
            api_key=endpoints.basic_api_key,
        )
        model_slot = {"primary": primary, "fallback": dict(primary), "temperature": 0.7}
        rows["providers"] = {
            "providers": providers,
            "defaultModelConfig": {"baseModel": model_slot, "liteModel": model_slot},
            "customModelInfo": {},
        }
    if public_base:
        rows["personalSettings"] = {"publicIngressBaseUrl": public_base}
    return rows


@asynccontextmanager
async def seeded_config(public_base: str | None, *, model_id: str | None = None, with_model: bool = True) -> AsyncIterator[None]:
    """Store the model providers and the public address as the instance's own config rows, then restore the rows.

    ``public_base=None`` leaves the instance without a public address; ``model_id`` names another model than the one of
    ``.env.test`` on the same endpoint (a model the endpoint does not serve makes every turn fail at its model call);
    ``with_model=False`` leaves it without any model provider, as an instance that was never set up.
    """
    rows = _config_rows(public_base, model_id, with_model=with_model)
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
    try:
        yield
    finally:
        async with get_session() as session:
            await session.execute(delete(UserConfig).where(UserConfig.config_key.in_(_SEEDED_KEYS)))
            session.add_all(saved)
            await session.commit()
        invalidate_user_configs_cache()


@asynccontextmanager
async def running_stack(
    capabilities: ChannelCapabilities,
    *,
    reaction_level: ReactionLevel = ReactionLevel.OFF,
    groups: frozenset[str] = frozenset(),
) -> AsyncIterator[Stack]:
    """A running router and bus whose only channel is the recording platform edge, with the real executor.

    The agent runs without the web search tool: an agent that has it is only ready while a live search service answers
    a probe, and that service is an external dependency this hand-off chain does not touch.
    """
    without_search = tuple(tool for tool in preamble.DEFAULT_ENABLED_BUILTIN_TOOLS if tool != "web_search")
    with mock.patch.object(preamble, "DEFAULT_ENABLED_BUILTIN_TOOLS", without_search):
        bus = MessageBus()
        channel = RecordingChannel(capabilities)
        bus.register_channel(channel)
        router = AgentRouter(
            bus=bus,
            pairing_store=_OwnerPairingStore(),
            agent_executor=ChannelAgentExecutor(),
            policy_provider=_OpenPolicyProvider(reaction_level, groups),
            session_gate_config=SessionGateConfig(debounce_window_ms=0),
        )
        await bus.start()
        await router.start()
        try:
            yield Stack(router=router, channel=channel)
        finally:
            await router.stop()
            await bus.stop()


async def stored_chat_ids() -> list[str]:
    """The ids of the chats the conversations of the test channel are stored in."""
    async with get_session() as session:
        return list((await session.execute(select(Chat.id).where(Chat.source == CHANNEL))).scalars())


@asynccontextmanager
async def stored_chats_removed() -> AsyncIterator[None]:
    """Hard-delete the chats of the test channel on exit, so no row outlives the test.

    A chat is deleted for good only from the trash, so each goes to the trash first.
    """
    try:
        yield
    finally:
        for chat_id in await stored_chat_ids():
            assert await ChatService.delete_chat(chat_id), f"chat {chat_id} did not go to the trash"
            assert await ChatService.permanently_delete_chat(chat_id), f"chat {chat_id} was not deleted for good"


def direct_message(
    sender: str, text: str, *, locale: str | None = None, is_group: bool = False, chat: str | None = None
) -> InboundMessage:
    """A message of ``sender`` to the bot, in the direct chat of the sender unless ``chat`` names a group."""
    return InboundMessage(
        channel=CHANNEL,
        sender_id=sender,
        chat_id=chat or sender,
        content=text,
        message_id=f"{CHANNEL}-{uuid.uuid4().hex[:12]}",
        is_group=is_group,
        mentioned=is_group,
        metadata={"locale": locale} if locale else {},
    )


async def reply_containing(
    stack: Stack, origin: InboundMessage, marker: str, *, start: int = 0, timeout_sec: float = REPLY_TIMEOUT_SEC
) -> OutboundMessage:
    """The first message the platform was asked to say that contains ``marker`` (any message for an empty marker).

    ``start`` skips the messages said before it, for a test that waits for what follows something it already read. A
    conversation that has gone idle after something was said without the marker fails at once: that message is the
    turn's error reply, and waiting out the full timeout would only hide it.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_sec
    idle_since: float | None = None
    while loop.time() < deadline:
        said = stack.channel.said[start:]
        for message in said:
            if marker in message.content:
                return message
        if said and not stack.router.is_session_busy(origin):
            idle_since = idle_since if idle_since is not None else loop.time()
            if loop.time() - idle_since > _IDLE_GRACE_SEC:
                break
        else:
            idle_since = None
        await asyncio.sleep(0.2)
    pytest.fail(f"nothing said {marker!r}; said: {[m.content[:300] for m in stack.channel.said]}")


def handoff_buttons(message: OutboundMessage) -> list[ActionButton]:
    """The hand-off buttons of ``message``."""
    return [
        button
        for row in message.components
        for button in row
        if isinstance(button, ActionButton) and button.action_id == HANDOFF_ACTION
    ]


def handoff_chat_id(message: OutboundMessage) -> str:
    """The chat the single hand-off button of ``message`` opens, after checking the link is built on the public address."""
    buttons = handoff_buttons(message)
    assert len(buttons) == 1, f"expected one {HANDOFF_ACTION} button, got {message.components!r}"
    url = buttons[0].url or ""
    assert url.startswith(f"{PUBLIC_BASE}/"), url
    chat_id = url.removeprefix(f"{PUBLIC_BASE}/")
    assert chat_id and "/" not in chat_id, url
    logger.info("hand-off button under %r opens %s", message.content[:80], url)
    return chat_id


def linked_chat_id(content: str) -> str:
    """The chat id of the hand-off link a message carries as text: where a channel has no buttons the link is in the content."""
    found = re.findall(rf"{re.escape(PUBLIC_BASE)}/([A-Za-z0-9-]+)", content)
    assert len(found) == 1, f"expected one hand-off link in {content!r}"
    return found[0]


async def stored_texts(chat_id: str) -> tuple[list[str], list[str]]:
    """(user texts, assistant texts) of the stored chat, oldest first."""
    messages = await ChatService.get_all_messages(chat_id)
    return (
        [m.content for m in messages if m.role == "user"],
        [m.content for m in messages if m.role == "assistant"],
    )
