"""Builders of the action-button approval tests: the router host under test, a click, an approval record, the database."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.channels.routing.commands import RouterCommandsMixin
from app.channels.routing.pending_approvals import PendingApprovals
from app.channels.routing.router_models import _ActiveTask
from app.channels.types import InboundMessage
from app.database.models.approval import ApprovalRecord
from tests.support.pending_approvals import empty_registry, finished_turn

engine = create_async_engine("sqlite+aiosqlite:///:memory:")
_factory = async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    async with _factory() as db:
        try:
            yield db
        finally:
            await db.close()


def active_turn(requester_id: str = "alice") -> _ActiveTask:
    """The registry entry of a finished turn of the clicks' chat (``telegram``/``chat-1``) started by ``requester_id``."""
    return finished_turn(requester_id, chat_id="chat-1")


@dataclass(slots=True)
class Host(RouterCommandsMixin):
    """What the approval handlers read of the router, over a bus and a gate that record what they are given."""

    _active_tasks: dict[str, _ActiveTask] = field(default_factory=dict)
    _pending_approvals: PendingApprovals = field(default_factory=empty_registry)
    _bus: MagicMock = field(default_factory=MagicMock)
    _gate: MagicMock = field(default_factory=MagicMock)
    # The router decides who the owner is; each test states whether the clicker passes that check.
    _check_admin_permission: AsyncMock = field(default_factory=lambda: AsyncMock(return_value=True))


def make_host() -> Host:
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    bus.edit_channel_message = AsyncMock()
    gate = MagicMock()
    gate.submit = MagicMock()
    return Host(_bus=bus, _gate=gate)


def click(
    content: str,
    *,
    sender_id: str = "alice",
    channel: str = "telegram",
    chat_id: str = "chat-1",
    is_group: bool = False,
    origin_message_id: str | None = "orig-123",
    locale: str | None = None,
) -> InboundMessage:
    """What an approval card's button sends when ``sender_id`` presses it."""
    metadata: dict[str, object] = {}
    if origin_message_id:
        metadata["origin_message_id"] = origin_message_id
    if locale:
        metadata["locale"] = locale
    metadata["username"] = sender_id
    return InboundMessage(
        channel=channel,
        sender_id=sender_id,
        content=content,
        chat_id=chat_id,
        is_group=is_group,
        metadata=metadata,
    )


async def seed(record_id: str = "appr-001", status: str = "PENDING", action_type: str = "shell_command") -> str:
    """Store an approval record of the chat ``chat-1`` (replacing one of the same id) and return its id."""
    async with session() as db:
        existing = await db.get(ApprovalRecord, record_id)
        if existing:
            await db.delete(existing)
            await db.commit()
        db.add(
            ApprovalRecord(
                id=record_id,
                agent_id="agent-1",
                chat_id="chat-1",
                thread_id="thread-1",
                action_type=action_type,
                reason="test",
                severity="warning",
                payload={"cmd": "ls"},
                status=status,
            )
        )
        await db.commit()
    return record_id


async def status_of(record_id: str) -> str:
    async with session() as db:
        record = await db.get(ApprovalRecord, record_id)
        assert record is not None
        return record.status
