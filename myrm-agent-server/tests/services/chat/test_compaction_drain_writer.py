"""The idle drain stores its summary through the writer every compaction shares.

The model call takes seconds, during which a rewind or an undo may remove the row the summary was cut at; the shared
writer refuses such a boundary and the drain drops the summary instead of storing one of rows that are gone. The salient
tool archive is appended after the writer's index rebuild, which drops every segment no message backs.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.services.chat.chat_compaction import _ChatCompactionMixin
from tests.support.privacy_wire import wire_llm

CHAT_ID = "chat-drain"
SNAPSHOT_ID = "m2"


class _Run:
    """What one drain did, in order."""

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.persist = AsyncMock(side_effect=self._persist)
        self.archive = AsyncMock(side_effect=self._archive)
        self.boundary_removed = False

    async def _persist(self, *_args: object) -> str:
        self.calls.append("persist")
        if self.boundary_removed:
            raise ValueError("Compaction boundary m2 no longer exists")
        return SNAPSHOT_ID

    async def _archive(self, *_args: object, **_kwargs: object) -> list[object]:
        self.calls.append("archive")
        return []


@pytest.fixture
def run() -> Iterator[_Run]:
    drain = _Run()
    llm, _sent = wire_llm('{"goal": "notes"}')
    stored = [
        SimpleNamespace(id="m1", role="user", content="question", extra_data=None),
        SimpleNamespace(id=SNAPSHOT_ID, role="assistant", content="answer", extra_data=None),
    ]
    repo = SimpleNamespace(
        get_latest_message=AsyncMock(
            return_value=SimpleNamespace(id=SNAPSHOT_ID, created_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(hours=1))
        ),
        get_chat_by_id=AsyncMock(return_value=SimpleNamespace(compacted_before_id=None)),
        get_all_messages=AsyncMock(return_value=stored),
    )
    model_cfg = SimpleNamespace(max_context_tokens=8000, api_keys=None)

    class _Uow:
        session = object()

        async def __aenter__(self) -> _Uow:
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def commit(self) -> None:
            drain.calls.append("commit")

    with (
        patch("asyncio.sleep", new=AsyncMock()),
        patch("app.services.chat.chat_compaction.UnitOfWork", new=_Uow),
        patch("app.services.chat.chat_compaction.do_persist_to_db", new=drain.persist),
        patch("app.services.chat.chat_compaction._ChatServiceBase._cr", new=staticmethod(lambda _uow: repo)),
        patch(
            "app.core.channel_bridge.config_loader.load_user_configs",
            new=AsyncMock(return_value=SimpleNamespace(providers_dict=None, personal_settings_dict={})),
        ),
        patch("app.core.channel_bridge.model_resolver.resolve_model_config", new=lambda _providers: model_cfg),
        patch("app.core.channel_bridge.model_resolver.enrich_model_context_window", new=lambda cfg, _providers: cfg),
        patch("myrm_agent_harness.toolkits.llms.llm_manager.get_llm_from_config", new=AsyncMock(return_value=llm)),
        patch(
            "app.services.chat.salient_tool_archive_service.SalientToolArchiveService.archive_salient_tools",
            new=drain.archive,
        ),
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
