"""A compaction keeps its summary and its failure cooldown while other writers commit around the model call.

The model needs seconds to minutes for a summary. Under the deferred-read transaction policy a session that has read the
chat keeps its snapshot of the whole database, so any other commit during the model call (another chat's message, a
background job) aborts the summary's own write with SQLITE_BUSY_SNAPSHOT (``database is locked``, which
``busy_timeout`` does not cover) and the paid-for summary is thrown away. The cooldown after a failed compaction has to
reach the database too, or the next idle turn pays for the same failing summary again. The Pipeline's own persist
callback runs beside a live turn that keeps committing messages, so its summary needs the same protection. The tests
run the real transaction policy on a file database; the plain in-memory engines do not run it.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import ExitStack
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from myrm_agent_harness.agent.context_management.strategies.compression.compression_streak_store import (
    InMemoryCompressionStreakStore,
    register_compression_streak_store,
)
from myrm_agent_harness.agent.context_management.tracking.task_metrics import clear_task_metrics
from sqlalchemy import select

from app.database.dto import MessageDTO
from app.database.models import Chat
from app.database.repositories.chat_repo import ChatRepository
from app.services.chat.compact.compression_streak import ChatCompressionStreakStore
from app.services.chat.compact_service import compact_chat, is_compaction_failure_cooldown_active, persist_compaction
from tests.services.chat.compaction_support import make_messages
from tests.support.policy_sqlite import PolicyDb, policy_database, run_while_another_writer_commits

_SERVICE = "app.services.chat.compact.service"
_CIRCUIT = "myrm_agent_harness.agent.context_management.strategies.summary.summarize_circuit_guard.is_summarize_circuit_open"
_FIRST_MESSAGE_AT = datetime(2025, 1, 1, 12, 0, 0)
_MESSAGES = 12
_SUMMARY = '{"user_goal":"review"}'
_WINDOW = 128_000

Summarize = Callable[..., Awaitable[tuple[list[object], MagicMock]]]


@pytest_asyncio.fixture
async def policy_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[PolicyDb]:
    async with policy_database(tmp_path / "compact.db", monkeypatch) as db:
        yield db


@pytest.fixture
def chat_id() -> Iterator[str]:
    """A chat id whose anti-thrash streak lives in this process only, so the test needs no streak table."""
    chat = f"chat-compact-{uuid4().hex}"
    register_compression_streak_store(InMemoryCompressionStreakStore())
    yield chat
    register_compression_streak_store(None)
    clear_task_metrics(chat)


async def _seed_chat(db: PolicyDb, chat_id: str) -> None:
    messages = [
        MessageDTO(
            id=f"msg-{chat_id}-{index}",
            chat_id=chat_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"message {index}",
            sent_at=_FIRST_MESSAGE_AT + timedelta(minutes=index),
            sent_timezone="UTC",
            created_at=_FIRST_MESSAGE_AT + timedelta(minutes=index),
        )
        for index in range(_MESSAGES)
    ]
    async with db.factory() as session:
        session.add(Chat(id=chat_id, title="Test", source="web"))
        await session.flush()
        await ChatRepository.add_messages(session, messages)
        await session.commit()


async def _commit_another_chat(db: PolicyDb) -> None:
    """Any commit moves the database past the snapshot a reading session holds, whichever table it touches."""
    async with db.factory() as other:
        other.add(Chat(id=f"other-writer-{uuid4().hex}"))
        await other.commit()


def _summarizer(db: PolicyDb, *, another_writer_commits: bool, error: BaseException | None = None) -> Summarize:
    """The model call: another writer commits while it runs; it then answers with a summary, or fails with ``error``."""
    summary = MagicMock()
    summary.to_json.return_value = _SUMMARY

    async def summarize(**_arguments: object) -> tuple[list[object], MagicMock]:
        if another_writer_commits:
            await _commit_another_chat(db)
        if error is not None:
            raise error
        return [], summary

    return summarize


def _collaborators(summarize: Summarize) -> ExitStack:
    """Stand in for what is not under test: the model's window and call, the backup file and the recall index."""
    stack = ExitStack()
    replacements: dict[str, object] = {
        _CIRCUIT: MagicMock(return_value=False),
        f"{_SERVICE}.get_llm_for_user": AsyncMock(return_value=(MagicMock(), _WINDOW)),
        f"{_SERVICE}.guarded_compact_summarize": summarize,
        f"{_SERVICE}.backup_context": AsyncMock(return_value="/tmp/backup.jsonl"),
        "app.services.chat.compact.persist.ConversationRecallIndexService.rebuild_chat": AsyncMock(),
    }
    for target, replacement in replacements.items():
        stack.enter_context(patch(target, replacement))
    return stack


def _reads_stubbed(chat_id: str) -> ExitStack:
    """Hand ``compact_chat`` its chat, messages and streak, so the first database work it does is the summary's write."""
    stack = ExitStack()
    replacements: dict[str, object] = {
        f"{_SERVICE}.load_chat": AsyncMock(return_value=Chat(id=chat_id)),
        f"{_SERVICE}.load_compactable_messages": AsyncMock(return_value=make_messages(chat_id, _MESSAGES)),
        "app.services.chat.compact.compression_streak.hydrate_compression_streak_from_db": AsyncMock(return_value=0),
    }
    for target, replacement in replacements.items():
        stack.enter_context(patch(target, replacement))
    return stack


async def _stored_chat(db: PolicyDb, chat_id: str) -> Chat:
    async with db.factory() as session:
        return (await session.execute(select(Chat).where(Chat.id == chat_id))).scalar_one()


@pytest.mark.asyncio
async def test_the_summary_is_stored_when_another_writer_commits_while_the_model_writes_it(
    policy_db: PolicyDb, chat_id: str
) -> None:
    await _seed_chat(policy_db, chat_id)

    with _collaborators(_summarizer(policy_db, another_writer_commits=True)):
        async with policy_db.factory() as session:
            result = await compact_chat(session, chat_id)

    assert result.compacted is True
    assert (await _stored_chat(policy_db, chat_id)).compacted_summary == _SUMMARY


@pytest.mark.asyncio
async def test_the_summary_is_stored_when_another_writer_commits_between_its_read_and_its_write(
    policy_db: PolicyDb, chat_id: str
) -> None:
    await _seed_chat(policy_db, chat_id)

    async def compact() -> object:
        async with policy_db.factory() as session:
            return await compact_chat(session, chat_id)

    with _collaborators(_summarizer(policy_db, another_writer_commits=False)), _reads_stubbed(chat_id):
        await run_while_another_writer_commits(policy_db, compact)

    assert (await _stored_chat(policy_db, chat_id)).compacted_summary == _SUMMARY


@pytest.mark.asyncio
async def test_the_pipeline_persist_stores_the_summary_when_another_writer_commits_between_its_read_and_its_write(
    policy_db: PolicyDb, chat_id: str
) -> None:
    await _seed_chat(policy_db, chat_id)
    summary = MagicMock()
    summary.to_json.return_value = _SUMMARY

    async def persist() -> None:
        await persist_compaction(chat_id, summary, f"msg-{chat_id}-{_MESSAGES - 1}", 100)

    rebuild_index = patch("app.services.chat.compact.persist.ConversationRecallIndexService.rebuild_chat", AsyncMock())
    with rebuild_index:
        await run_while_another_writer_commits(policy_db, persist)

    stored = await _stored_chat(policy_db, chat_id)
    assert stored.compacted_summary == _SUMMARY
    assert stored.compacted_before_id == f"msg-{chat_id}-{_MESSAGES - 1}"


@pytest.mark.asyncio
async def test_a_finished_compaction_stores_its_streak_and_leaves_nothing_pending_on_the_session(
    policy_db: PolicyDb, chat_id: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The streak reaches the chat row through the store's own connection, so the caller's session ends committed.

    A write flushed on that session after its commit is rolled back when the session closes, and holds the database
    write lock until then.
    """
    monkeypatch.setattr("app.config.settings.settings.database.sqlite_path", str(tmp_path / "compact.db"))
    register_compression_streak_store(ChatCompressionStreakStore())
    await _seed_chat(policy_db, chat_id)
    longer_than_the_messages = MagicMock()
    longer_than_the_messages.to_json.return_value = "review " * 2_000

    async def summarize(**_arguments: object) -> tuple[list[object], MagicMock]:
        return [], longer_than_the_messages

    with _collaborators(summarize):
        async with policy_db.factory() as session:
            result = await compact_chat(session, chat_id)
            assert not session.in_transaction()

    assert result.compacted is True and result.tokens_saved == 0
    assert (await _stored_chat(policy_db, chat_id)).compression_ineffective_streak == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("another_writer_commits", [False, True], ids=["alone", "another_writer_commits"])
@pytest.mark.parametrize(
    ("error", "reason"),
    [
        pytest.param(RuntimeError("provider quota exhausted"), None, id="summarize_error"),
        pytest.param(asyncio.TimeoutError("summary exceeded its time"), "timeout", id="timeout"),
    ],
)
async def test_a_failed_summary_leaves_a_cooldown_that_outlives_the_session(
    policy_db: PolicyDb, chat_id: str, error: BaseException, reason: str | None, another_writer_commits: bool
) -> None:
    await _seed_chat(policy_db, chat_id)

    with _collaborators(_summarizer(policy_db, another_writer_commits=another_writer_commits, error=error)):
        async with policy_db.factory() as session:
            if reason is None:
                # What failed is what the caller learns, not a database error raised while the cooldown was being written.
                with pytest.raises(type(error)):
                    await compact_chat(session, chat_id)
            else:
                assert (await compact_chat(session, chat_id)).reason == reason

    stored = await _stored_chat(policy_db, chat_id)
    assert stored.compacted_summary is None
    assert stored.compaction_failure_error and str(error) in stored.compaction_failure_error
    cooldown_until = stored.compaction_failure_cooldown_until
    assert cooldown_until is not None and cooldown_until.replace(tzinfo=UTC) > datetime.now(UTC)
    async with policy_db.factory() as session:
        active, _ = await is_compaction_failure_cooldown_active(session, chat_id)
    assert active is True
