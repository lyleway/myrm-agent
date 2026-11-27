"""Compaction settlement for changes to a chat's message history.

[INPUT]
- app.database.models::Chat (POS: compaction metadata)
- app.services.chat.conversation_recall_index_service::ConversationRecallIndexService (POS: index rebuild after deletion)
- app.core.utils.time_utils::as_utc (POS: zone-safe comparison)

[OUTPUT]
- settle_compaction_after_history_change: drop a summary the changed history made stale, refresh the recall index

[POS]
Runs in the transaction of every retry, regenerate, sibling switch, undo, truncate and rewind, so the summary and the
messages it was derived from never disagree for any reader.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.utils.time_utils import as_utc
from app.database.models import Chat
from app.services.chat.conversation_recall_index_service import ConversationRecallIndexService


async def settle_compaction_after_history_change(
    db: AsyncSession,
    chat_id: str,
    *,
    since: datetime,
    rows_deleted: bool,
) -> None:
    """Keep the compaction summary and the recall index true to the history a change just rewrote.

    ``since`` is when the earliest turn the change touches began. A summary written at or after it may carry what the
    change removed or replaced (a compaction that ran inside that turn included), so it is dropped; one written before
    it covers only turns the change left alone and stays, or every regenerate in a long chat would discard all older
    context. A boundary without a write time cannot be compared and is dropped as well. ``compacted_tokens_saved`` is
    a running total of what compaction ever saved, not part of the summary, so it is kept.

    The recall document text only grows, so rows that were deleted leave it only through a rebuild. Rows that were
    merely deactivated stay visible as sibling versions and need none.

    The caller holds the write lock and commits: the settlement reads the summary's write time and must not race a
    compaction that commits in between.
    """
    row = (await db.execute(select(Chat.compacted_before_id, Chat.compacted_at).where(Chat.id == chat_id))).one_or_none()
    if row is not None:
        boundary_id, compacted_at = row
        if boundary_id is not None and (compacted_at is None or as_utc(compacted_at) >= as_utc(since)):
            await db.execute(
                update(Chat).where(Chat.id == chat_id).values(compacted_summary=None, compacted_before_id=None, compacted_at=None)
            )
    if rows_deleted:
        await db.flush()
        await ConversationRecallIndexService.rebuild_chat(db, chat_id)
