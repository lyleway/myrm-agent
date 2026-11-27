"""Compaction DB persistence and failure cooldown.

[INPUT]
- app.database.factory::acquire_write_lock (POS: write lock before a read-then-write unit)
- app.database.models::Chat, Message (POS: ORM compaction metadata)
- app.services.chat.conversation_recall_index_service::ConversationRecallIndexService (POS: post-compact index rebuild)
- compact._constants::COMPACTION_FAILURE_COOLDOWN_SECONDS (POS: cooldown duration)
- app.core.utils.time_utils::as_utc (POS: zone-safe cooldown comparison)

[OUTPUT]
- do_persist_to_db: compaction metadata write under the write lock (caller commits)
- record_compaction_failure_cooldown / is_compaction_failure_cooldown_active: failure guards
- persist_compaction: Pipeline fire-and-forget wrapper

[POS]
Server DB persistence for ``compact_chat``, the idle drain and harness Pipeline compaction callbacks.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Protocol, cast

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time_utils import as_utc
from app.database.factory import acquire_write_lock
from app.database.models import Chat, Message
from app.services.chat.compact._constants import COMPACTION_FAILURE_COOLDOWN_SECONDS
from app.services.chat.conversation_recall_index_service import (
    ConversationRecallIndexService,
)

logger = logging.getLogger(__name__)


class _JsonSummary(Protocol):
    """A structured summary the Pipeline hands over; anything else is stored as its text."""

    def to_json(self) -> str: ...


async def do_persist_to_db(
    db: AsyncSession,
    chat_id: str,
    summary_text: str,
    before_message_id: str,
    tokens_saved: int,
) -> str:
    """Execute compaction metadata DB write (caller commits).

    The stored compaction boundary is read before the write, so the write lock is taken first: it leaves no moment
    between that read and the write for another writer to commit in, which would abort the write with
    SQLITE_BUSY_SNAPSHOT and lose a summary already paid for. Every caller (the manual path, the Pipeline callback and the
    idle drain) gets this from here, so none can forget it; the session's transaction must not have read anything yet.

    Raises:
        ValueError: no boundary can be resolved, or the one given no longer exists in the chat.
    """
    from sqlalchemy import desc

    await acquire_write_lock(db)

    effective_before_id: str | None = before_message_id or None
    if not effective_before_id:
        result = await db.execute(
            select(Message.id).where(Message.chat_id == chat_id).order_by(desc(Message.created_at)).limit(1)
        )
        effective_before_id = result.scalar_one_or_none()
    else:
        # A boundary the caller picked before its model call may be gone by now (a rewind or undo ran meanwhile);
        # a summary of removed messages must not be stored.
        boundary = await db.execute(select(Message.id).where(Message.chat_id == chat_id, Message.id == effective_before_id))
        if boundary.scalar_one_or_none() is None:
            raise ValueError(f"Compaction boundary {effective_before_id} no longer exists in chat_id={chat_id}")

    if not effective_before_id:
        raise ValueError(f"Cannot resolve before_message_id for chat_id={chat_id}")

    chat_result = await db.execute(select(Chat.compacted_before_id).where(Chat.id == chat_id))
    current_compacted_before_id = chat_result.scalar_one_or_none()

    if current_compacted_before_id and current_compacted_before_id != effective_before_id:
        ts_result = await db.execute(
            select(Message.id, Message.created_at).where(Message.id.in_([current_compacted_before_id, effective_before_id]))
        )
        timestamps = {row[0]: row[1] for row in ts_result.all()}

        current_ts = timestamps.get(current_compacted_before_id)
        target_ts = timestamps.get(effective_before_id)

        if current_ts and target_ts and current_ts >= target_ts:
            logger.warning("⚠️ [persist_compaction] DB has a newer or equal compaction boundary. Aborting overwrite.")
            return effective_before_id

    await db.execute(
        update(Chat)
        .where(Chat.id == chat_id)
        .values(
            compacted_summary=summary_text,
            compacted_before_id=effective_before_id,
            compacted_at=datetime.now(timezone.utc),
            compacted_tokens_saved=func.coalesce(Chat.compacted_tokens_saved, 0) + max(tokens_saved, 0),
            compaction_failure_cooldown_until=None,
            compaction_failure_error=None,
        )
    )
    await db.flush()
    await ConversationRecallIndexService.rebuild_chat(db, chat_id)

    return effective_before_id


async def persist_compaction(
    chat_id: str,
    summary: object,
    before_message_id: str,
    tokens_saved: int,
) -> None:
    """Persist compaction metadata (Pipeline fire-and-forget path)."""
    from app.database.connection import get_session

    summary_text: str
    if hasattr(summary, "to_json"):
        summary_text = cast(_JsonSummary, summary).to_json()
    else:
        summary_text = str(summary)

    async with get_session() as db:
        try:
            effective_before_id = await do_persist_to_db(db, chat_id, summary_text, before_message_id, tokens_saved)
            await db.commit()

            logger.warning(
                "💾 [persist_compaction] chat_id=%s, before_id=%s, tokens_saved=%d",
                chat_id,
                effective_before_id,
                tokens_saved,
            )
        except ValueError as exc:
            logger.warning("⚠️ [persist_compaction] %s", exc)
            await db.rollback()
        except Exception as exc:
            logger.exception(
                "❌ [persist_compaction] Unexpected error for chat_id=%s: %s",
                chat_id,
                exc,
            )
            await db.rollback()


async def record_compaction_failure_cooldown(
    db: AsyncSession,
    chat_id: str,
    error: str,
) -> None:
    until = datetime.now(timezone.utc) + timedelta(seconds=COMPACTION_FAILURE_COOLDOWN_SECONDS)
    await db.execute(
        update(Chat)
        .where(Chat.id == chat_id)
        .values(
            compaction_failure_cooldown_until=until,
            compaction_failure_error=error[:500],
        )
    )
    await db.flush()


async def is_compaction_failure_cooldown_active(
    db: AsyncSession,
    chat_id: str,
) -> tuple[bool, str | None]:
    """Return whether chat is in a post-failure compaction cooldown window."""
    row = await db.execute(
        select(Chat.compaction_failure_cooldown_until, Chat.compaction_failure_error).where(Chat.id == chat_id)
    )
    result = row.one_or_none()
    if result is None:
        return False, None
    cooldown_until, error = result
    if cooldown_until is None:
        return False, None
    if as_utc(cooldown_until) <= datetime.now(timezone.utc):
        return False, None
    return True, error
