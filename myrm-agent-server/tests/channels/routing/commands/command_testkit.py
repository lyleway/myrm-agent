"""Doubles for the tests of the IM slash-command handlers: an inbound message, a bus and a policy resolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from app.channels.types import InboundMessage


def make_command_msg(
    content: str = "",
    channel: str = "test",
    sender_id: str = "user1",
    chat_id: str | None = None,
    is_group: bool = False,
    thread_id: str | None = None,
    user_id: str | None = None,
) -> InboundMessage:
    """An inbound message with the fields a command handler reads."""
    return InboundMessage(
        channel=channel,
        sender_id=sender_id,
        content=content,
        chat_id=chat_id,
        is_group=is_group,
        thread_id=thread_id,
        user_id=user_id,
    )


def make_command_bus() -> MagicMock:
    """A bus whose outbound publishing is recorded and that has no channel."""
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    bus.get_channel = MagicMock(return_value=None)
    return bus


def make_command_resolver(user_id: str = "uid1") -> MagicMock:
    """A policy resolver that resolves every DM sender and group member to ``user_id``."""
    resolver = MagicMock()
    resolver.resolve_dm_user = AsyncMock(return_value=user_id)
    resolver.resolve_group_user = AsyncMock(return_value=(user_id, make_command_msg(is_group=True, user_id=user_id)))
    return resolver
