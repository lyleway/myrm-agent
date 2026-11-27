"""The idle drain stores its summary through the writer every compaction shares.

The model call takes seconds, during which a rewind or an undo may remove the row the summary was cut at; the shared
writer refuses such a boundary and the drain drops the summary instead of storing one of rows that are gone. The summary
is cut at the last active row the idle gate saw: a conversation Focus & Flush cleared while the drain waited costs no
model call, and a newest row that a sibling switch left inactive does not become the boundary. The salient tool archive
is appended after the writer's index rebuild, which drops every segment no message backs. What the drain adds to the
running total of saved tokens is measured as ``compact_chat`` measures it, so a row is counted once.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from myrm_agent_harness.utils.text_utils import get_token_count
from myrm_agent_harness.utils.token_estimation import estimate_messages_tokens
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.dto import MessageDTO
from app.database.models import Chat
from app.database.repositories.chat_repo import ChatRepository
from app.services.chat.chat_compaction import _ChatCompactionMixin
from tests.support.privacy_wire import wire_llm

CHAT_ID = "chat-drain"
SNAPSHOT_ID = "m2"
STORED_SUMMARY = '{"goal": "an earlier summary"}'
TOTAL_BEFORE = 1000
BODY = "lorem ipsum dolor sit amet consectetur " * 40


@contextmanager
def _model_run(llm: object, archive: AsyncMock) -> Iterator[None]:
    """The drain without its wait, with the user's configuration and model replaced by fixed ones."""
    model_cfg = SimpleNamespace(max_context_tokens=8000, api_keys=None)
    with (
        patch("asyncio.sleep", new=AsyncMock()),
        patch(
            "app.core.channel_bridge.config_loader.load_user_configs",
            new=AsyncMock(return_value=SimpleNamespace(providers_dict=None, personal_settings_dict={})),
        ),
        patch("app.core.channel_bridge.model_resolver.resolve_model_config", new=lambda _providers: model_cfg),
        patch("app.core.channel_bridge.model_resolver.enrich_model_context_window", new=lambda cfg, _providers: cfg),
        patch("myrm_agent_harness.toolkits.llms.llm_manager.get_llm_from_config", new=AsyncMock(return_value=llm)),
        patch(
            "app.services.chat.salient_tool_archive_service.SalientToolArchiveService.archive_salient_tools",
            new=archive,
        ),
    ):
        yield


LONG_AGO = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)


def _row(row_id: str, role: str, content: str, second: int) -> SimpleNamespace:
    return SimpleNamespace(
        id=row_id, role=role, content=content, extra_data=None, created_at=LONG_AGO + timedelta(seconds=second)
    )


class _Run:
    """What one drain did, in order, and the rows it finds: the active ones and the newest row the idle gate sees."""

    def __init__(self, sent: list[str]) -> None:
        self.calls: list[str] = []
        self.sent = sent
        self.persist = AsyncMock(side_effect=self._persist)
        self.archive = AsyncMock(side_effect=self._archive)
        self.boundary_removed = False
        self.rows = [_row("m1", "user", "question", 0), _row(SNAPSHOT_ID, "assistant", "answer", 1)]
        self.newest = self.rows[-1]

    async def _persist(self, *_args: object) -> str:
        self.calls.append("persist")
        if self.boundary_removed:
            raise ValueError("Compaction boundary m2 is no longer an active message")
        return SNAPSHOT_ID

    async def _archive(self, *_args: object, **_kwargs: object) -> list[object]:
        self.calls.append("archive")
        return []


@pytest.fixture
def run() -> Iterator[_Run]:
    llm, sent = wire_llm('{"goal": "notes"}')
    drain = _Run(sent)
    repo = SimpleNamespace(
        get_latest_message=AsyncMock(side_effect=lambda _chat_id: drain.newest),
        get_chat_by_id=AsyncMock(return_value=SimpleNamespace(compacted_before_id=None, compacted_summary=None)),
        get_all_messages=AsyncMock(side_effect=lambda _chat_id: drain.rows),
    )

    class _Uow:
        session = object()

        async def __aenter__(self) -> _Uow:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def commit(self) -> None:
            drain.calls.append("commit")

    with (
        _model_run(llm, drain.archive),
        patch("app.services.chat.chat_compaction.UnitOfWork", new=_Uow),
        patch("app.services.chat.chat_compaction.do_persist_to_db", new=drain.persist),
        patch("app.services.chat.chat_compaction._ChatServiceBase._cr", new=staticmethod(lambda _uow: repo)),
    ):
        yield drain


async def test_the_summary_is_stored_through_the_shared_writer_and_committed_before_the_archive(run: _Run) -> None:
    await _ChatCompactionMixin.flush_compaction_debt(CHAT_ID)

    assert run.calls == ["persist", "commit", "archive"]
    chat_id, _summary, boundary, tokens_saved = run.persist.await_args.args[1:]
    assert (chat_id, boundary) == (CHAT_ID, SNAPSHOT_ID)
    assert isinstance(tokens_saved, int) and tokens_saved >= 0


async def test_a_boundary_removed_while_the_model_ran_discards_the_summary(run: _Run) -> None:
    run.boundary_removed = True

    await _ChatCompactionMixin.flush_compaction_debt(CHAT_ID)

    assert run.calls == ["persist"], "nothing is committed or archived for a summary of rows that are gone"


async def test_a_conversation_flushed_while_the_drain_waited_costs_no_model_call(run: _Run) -> None:
    run.rows = []

    await _ChatCompactionMixin.flush_compaction_debt(CHAT_ID)

    assert run.calls == [] and run.sent == [], "no active message is left, so nothing is summarized, stored or archived"


async def test_a_newest_row_a_sibling_switch_left_inactive_is_not_the_boundary(run: _Run) -> None:
    run.newest = _row("m2b", "assistant", "the other version", 2)

    await _ChatCompactionMixin.flush_compaction_debt(CHAT_ID)

    assert run.persist.await_args.args[3] == SNAPSHOT_ID


async def test_rows_written_after_the_gate_stay_out_of_the_summary(run: _Run) -> None:
    run.rows.append(_row("m3", "user", "LATER-ROW", 2))

    await _ChatCompactionMixin.flush_compaction_debt(CHAT_ID)

    assert run.persist.await_args.args[3] == SNAPSHOT_ID
    assert not any("LATER-ROW" in text for text in run.sent)


async def test_the_running_total_counts_what_the_stored_summary_did_not_already_cover(db_session: AsyncSession) -> None:
    """A chat compacted up to ``m3`` already counted the saving of ``m0``..``m3``; the drain adds only the rest."""
    first_at = datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1)
    db_session.add(Chat(id=CHAT_ID, title="Test", source="web"))
    await db_session.flush()
    turns = [(f"m{i}", "user" if i % 2 == 0 else "assistant", f"{BODY}{i}", first_at + timedelta(seconds=i)) for i in range(8)]
    await ChatRepository.add_messages(
        db_session,
        [
            MessageDTO(id=row_id, chat_id=CHAT_ID, role=role, content=content, sent_at=at, sent_timezone="UTC", created_at=at)
            for row_id, role, content, at in turns
        ],
    )
    await db_session.execute(
        update(Chat)
        .where(Chat.id == CHAT_ID)
        .values(
            compacted_summary=STORED_SUMMARY,
            compacted_before_id="m3",
            compacted_at=first_at + timedelta(seconds=4),
            compacted_tokens_saved=TOTAL_BEFORE,
        )
    )
    await db_session.commit()
    llm, _sent = wire_llm('{"goal": "notes"}')

    with _model_run(llm, AsyncMock(return_value=[])):
        await _ChatCompactionMixin.flush_compaction_debt(CHAT_ID)

    new_summary, boundary, total = (
        await db_session.execute(
            select(Chat.compacted_summary, Chat.compacted_before_id, Chat.compacted_tokens_saved).where(Chat.id == CHAT_ID)
        )
    ).one()

    def read(rows: list[tuple[str, str, str, datetime]]) -> list[BaseMessage]:
        return [HumanMessage(content=c) if role == "user" else AIMessage(content=c) for _id, role, c, _at in rows]

    replaced = estimate_messages_tokens(read(turns[4:])) + get_token_count(STORED_SUMMARY)
    assert boundary == "m7"
    assert total - TOTAL_BEFORE == replaced - get_token_count(new_summary)
    whole_history = estimate_messages_tokens(read(turns)) - get_token_count(new_summary)
    assert total - TOTAL_BEFORE < whole_history, "the rows the stored summary covered must not be counted a second time"
