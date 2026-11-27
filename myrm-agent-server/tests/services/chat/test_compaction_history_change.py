"""A change to a chat's history settles the compaction summary and the recall index in the change's own transaction.

A summary is derived from the messages written before it. Once retry, regenerate, a sibling switch, undo, truncate or a
rewind rewrites the history from some turn on, a summary written at or after that turn may carry what was removed or
replaced and is dropped; one written before it covers only untouched turns and stays, or every regenerate in a long chat
would discard all older context. Rows a change deleted also leave the recall index, whose text only grows otherwise.
A compaction that picked its boundary before the change must not store a summary of rows that are gone, and a reply
the change replaced must not be read into the next summary.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.dto import MessageDTO
from app.database.models import Chat, Message
from app.database.repositories.chat_repo import ChatRepository
from app.services.chat.chat_service import ChatService
from app.services.chat.compact.history_change import settle_compaction_after_history_change
from app.services.chat.compact.message_io import db_messages_to_langchain, load_compactable_messages
from app.services.chat.compact.persist import do_persist_to_db
from app.services.chat.conversation_recall_index_service import ConversationRecallIndexService

pytestmark = pytest.mark.usefixtures("db_session", "rewind_collaborators")

CHAT_ID = "chat-settle"
FIRST_ROW_AT = datetime(2025, 1, 1, 12, 0, 0)
SUMMARY = "SUMMARY of the conversation"
TOKENS_SAVED = 100
DELETED_ANSWER = "answer SECRET-A2"


def _at(second: int) -> datetime:
    return FIRST_ROW_AT + timedelta(seconds=second)


def _message(
    message_id: str, role: str, content: str, second: int, *, group: str | None = None, active: bool = True
) -> MessageDTO:
    return MessageDTO(
        id=message_id,
        chat_id=CHAT_ID,
        role=role,
        content=content,
        sent_at=_at(second),
        sent_timezone="UTC",
        created_at=_at(second),
        sibling_group_id=group,
        is_active=active,
    )


async def _seed(db: AsyncSession, *, regenerated: bool = False) -> None:
    """Two turns; with ``regenerated`` the answer of the second has an older and a newer version in group ``g``."""
    db.add(Chat(id=CHAT_ID, title="Test", source="web"))
    await db.flush()
    group = "g" if regenerated else None
    rows = [
        _message("u1", "user", "Q1", 0),
        _message("a1", "assistant", "A1", 1),
        _message("u2", "user", "Q2", 2),
        _message("a2", "assistant", DELETED_ANSWER, 3, group=group, active=not regenerated),
    ]
    if regenerated:
        rows.append(_message("a2b", "assistant", "answer B", 4, group=group))
    await ChatRepository.add_messages(db, rows)
    await db.commit()
    await ConversationRecallIndexService.rebuild_chat(db, CHAT_ID)
    await db.commit()


async def _store_summary(db: AsyncSession, written_at: datetime | None) -> None:
    await db.execute(
        update(Chat)
        .where(Chat.id == CHAT_ID)
        .values(
            compacted_summary=SUMMARY,
            compacted_before_id="a2",
            compacted_at=written_at,
            compacted_tokens_saved=TOKENS_SAVED,
        )
    )
    await db.commit()


async def _compaction(db: AsyncSession) -> tuple[str | None, str | None, datetime | None, int]:
    row = (
        await db.execute(
            select(Chat.compacted_summary, Chat.compacted_before_id, Chat.compacted_at, Chat.compacted_tokens_saved).where(
                Chat.id == CHAT_ID
            )
        )
    ).one()
    return row[0], row[1], row[2], row[3]


async def _recall_text(db: AsyncSession) -> str:
    result = await db.execute(
        text("SELECT searchable_text FROM conversation_recall_documents WHERE chat_id = :chat_id"), {"chat_id": CHAT_ID}
    )
    return result.scalar_one()


class TestSettlement:
    @pytest.mark.parametrize("since_zone", [None, timezone.utc], ids=["naive", "aware"])
    async def test_a_summary_written_after_the_change_began_is_dropped_whatever_the_zone_of_the_times(
        self, db_session: AsyncSession, since_zone: timezone | None
    ) -> None:
        await _seed(db_session)
        await _store_summary(db_session, _at(10))

        await settle_compaction_after_history_change(
            db_session, CHAT_ID, since=_at(5).replace(tzinfo=since_zone), rows_deleted=False
        )
        await db_session.commit()

        summary, boundary, written_at, saved = await _compaction(db_session)
        assert (summary, boundary, written_at) == (None, None, None)
        assert saved == TOKENS_SAVED, "the running total of what compaction saved is not part of the summary"

    async def test_a_summary_written_at_the_moment_the_change_began_is_dropped(self, db_session: AsyncSession) -> None:
        await _seed(db_session)
        await _store_summary(db_session, _at(5))

        await settle_compaction_after_history_change(db_session, CHAT_ID, since=_at(5), rows_deleted=False)

        assert (await _compaction(db_session))[0] is None

    async def test_a_summary_written_before_the_change_began_stays(self, db_session: AsyncSession) -> None:
        await _seed(db_session)
        await _store_summary(db_session, _at(1))

        await settle_compaction_after_history_change(db_session, CHAT_ID, since=_at(5), rows_deleted=False)

        assert await _compaction(db_session) == (SUMMARY, "a2", _at(1), TOKENS_SAVED)

    async def test_a_boundary_without_a_write_time_cannot_be_compared_and_is_dropped(self, db_session: AsyncSession) -> None:
        await _seed(db_session)
        await _store_summary(db_session, None)

        await settle_compaction_after_history_change(db_session, CHAT_ID, since=_at(5), rows_deleted=False)

        assert (await _compaction(db_session))[:3] == (None, None, None)

    async def test_a_chat_without_a_summary_is_left_alone(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        await settle_compaction_after_history_change(db_session, CHAT_ID, since=_at(0), rows_deleted=False)

        assert await _compaction(db_session) == (None, None, None, None)

    async def test_rows_deleted_by_the_change_leave_the_recall_index(self, db_session: AsyncSession) -> None:
        await _seed(db_session)
        assert DELETED_ANSWER in await _recall_text(db_session)
        await db_session.execute(Message.__table__.delete().where(Message.id == "a2"))

        await settle_compaction_after_history_change(db_session, CHAT_ID, since=_at(2), rows_deleted=True)

        assert DELETED_ANSWER not in await _recall_text(db_session)

    async def test_a_change_that_deleted_nothing_does_not_rebuild_the_index(self, db_session: AsyncSession) -> None:
        await _seed(db_session)
        await db_session.execute(Message.__table__.delete().where(Message.id == "a2"))

        await settle_compaction_after_history_change(db_session, CHAT_ID, since=_at(2), rows_deleted=False)

        assert DELETED_ANSWER in await _recall_text(db_session)


async def _retry() -> object:
    return await ChatService.retry_last_turn(CHAT_ID)


async def _undo() -> object:
    return await ChatService.undo_last_turn(CHAT_ID)


async def _regenerate() -> object:
    return await ChatService.regenerate_last_turn(CHAT_ID)


async def _truncate() -> object:
    return await ChatService.truncate_after_message(CHAT_ID, "u2")


async def _rewind() -> object:
    return await ChatService.rewind_to_message(CHAT_ID, "u2")


async def _switch() -> object:
    return await ChatService.switch_sibling(CHAT_ID, "g", "a2b")


# Each path with the second its change begins at: the turn of "u2", or for a sibling switch the first version of the answer.
PATHS: dict[str, tuple[Callable[[], Awaitable[object]], int]] = {
    "retry": (_retry, 2),
    "undo": (_undo, 2),
    "regenerate": (_regenerate, 2),
    "truncate": (_truncate, 2),
    "rewind": (_rewind, 2),
    "switch": (_switch, 3),
}
DELETING_PATHS = ["retry", "undo", "truncate", "rewind"]


class TestEveryPathSettles:
    @pytest.mark.parametrize("path", PATHS)
    async def test_a_summary_written_during_or_after_the_changed_turn_is_dropped(
        self, db_session: AsyncSession, path: str
    ) -> None:
        action, begins_at = PATHS[path]
        await _seed(db_session, regenerated=path == "switch")
        await _store_summary(db_session, _at(begins_at + 1))

        await action()

        assert (await _compaction(db_session))[:3] == (None, None, None)
        assert (await _compaction(db_session))[3] == TOKENS_SAVED

    @pytest.mark.parametrize("path", PATHS)
    async def test_a_summary_written_before_the_changed_turn_stays(self, db_session: AsyncSession, path: str) -> None:
        action, begins_at = PATHS[path]
        await _seed(db_session, regenerated=path == "switch")
        await _store_summary(db_session, _at(begins_at - 1))

        await action()

        assert await _compaction(db_session) == (SUMMARY, "a2", _at(begins_at - 1), TOKENS_SAVED)

    @pytest.mark.parametrize("path", DELETING_PATHS)
    async def test_deleted_rows_leave_the_recall_index(self, db_session: AsyncSession, path: str) -> None:
        await _seed(db_session)
        assert DELETED_ANSWER in await _recall_text(db_session)

        await PATHS[path][0]()

        assert DELETED_ANSWER not in await _recall_text(db_session)

    async def test_a_chat_that_was_never_compacted_is_changed_as_before(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        result = await ChatService.undo_last_turn(CHAT_ID)

        assert result.success is True
        assert result.deleted_count == 2
        assert await _compaction(db_session) == (None, None, None, None)


class TestACompactionThatPickedItsBoundaryBeforeAChange:
    async def test_a_boundary_the_change_removed_is_refused(self, db_session: AsyncSession) -> None:
        await _seed(db_session)
        await ChatService.undo_last_turn(CHAT_ID)

        with pytest.raises(ValueError, match="no longer exists"):
            await do_persist_to_db(db_session, CHAT_ID, SUMMARY, "a2", TOKENS_SAVED)

        assert await _compaction(db_session) == (None, None, None, None)

    async def test_a_boundary_of_another_chat_is_refused(self, db_session: AsyncSession) -> None:
        await _seed(db_session)
        db_session.add(Chat(id="other-chat", title="Other", source="web"))
        await db_session.commit()

        with pytest.raises(ValueError, match="no longer exists"):
            await do_persist_to_db(db_session, "other-chat", SUMMARY, "a2", TOKENS_SAVED)

    async def test_an_existing_boundary_is_stored(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        stored = await do_persist_to_db(db_session, CHAT_ID, SUMMARY, "a1", TOKENS_SAVED)
        await db_session.commit()

        assert stored == "a1"
        assert (await _compaction(db_session))[:2] == (SUMMARY, "a1")

    async def test_without_a_boundary_the_newest_row_is_used(self, db_session: AsyncSession) -> None:
        await _seed(db_session)

        stored = await do_persist_to_db(db_session, CHAT_ID, SUMMARY, "", TOKENS_SAVED)

        assert stored == "a2"


class TestWhatTheNextCompactionReads:
    async def test_a_reply_a_regenerate_replaced_is_not_summarized(self, db_session: AsyncSession) -> None:
        await _seed(db_session, regenerated=True)
        chat = await db_session.get(Chat, CHAT_ID)

        rows = await load_compactable_messages(db_session, chat)

        assert [row.id for row in rows] == ["u1", "a1", "u2", "a2b"]
        assert DELETED_ANSWER not in [message.content for message in db_messages_to_langchain(rows)]
