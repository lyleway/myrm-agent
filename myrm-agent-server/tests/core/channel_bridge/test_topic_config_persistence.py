"""A topic binding survives what runs beside it: an edit made through another manager and a writer that holds the database."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.channel_bridge.topic_config import SqlTopicManager
from app.database import connection
from app.database.models import UserConfig
from app.database.repositories.uow import UnitOfWork


@pytest.fixture(autouse=True)
def bindable_agents() -> Iterator[None]:
    """Any agent id binds: these tests are about storage, not about which agents exist."""
    with (
        patch.object(SqlTopicManager, "_resolve_agent_id", AsyncMock(side_effect=lambda agent_id: agent_id)),
        patch.object(SqlTopicManager, "_assert_channel_bindable_agent", AsyncMock(return_value=None)),
        patch.object(SqlTopicManager, "_is_channel_bindable_agent", AsyncMock(return_value=True)),
    ):
        yield


@pytest.fixture
async def channel() -> AsyncIterator[str]:
    """A channel of its own: the stored config and its write generation are per channel."""
    name = f"persist{uuid.uuid4().hex[:8]}"
    yield name
    async with UnitOfWork(write=True) as uow:
        assert uow.session is not None
        await uow.session.execute(delete(UserConfig).where(UserConfig.config_key.like(f"%{name}%")))


async def _agent_of(manager: SqlTopicManager, channel: str, chat_id: str) -> str | None:
    topic = await manager.resolve_topic(channel, chat_id, None)
    return topic.agent_id if topic else None


async def _serve_from_cache(manager: SqlTopicManager, channel: str, chat_id: str) -> None:
    """Resolve until the manager answers from its cache: the first resolutions still flush the last-active time."""
    for _ in range(3):
        await _agent_of(manager, channel, chat_id)


async def test_a_manager_serves_a_binding_edited_through_another_manager_at_once(channel: str) -> None:
    router, api = SqlTopicManager(), SqlTopicManager()
    await api.bind_topic(channel, "chat-1", None, agent_id="agent-x")
    await _serve_from_cache(router, channel, "chat-1")
    assert await _agent_of(router, channel, "chat-1") == "agent-x"

    await api.bind_topic(channel, "chat-1", None, agent_id="agent-y")

    assert await _agent_of(router, channel, "chat-1") == "agent-y"


async def test_the_last_active_flush_of_a_stale_manager_does_not_undo_an_edit(channel: str) -> None:
    router, api = SqlTopicManager(), SqlTopicManager()
    await api.bind_topic(channel, "chat-1", None, agent_id="agent-x")
    await _serve_from_cache(router, channel, "chat-1")
    await api.bind_topic(channel, "chat-1", None, agent_id="agent-y")

    with patch("app.core.channel_bridge.topic_config.identity._ACTIVE_FLUSH_INTERVAL", 0.0):
        await _agent_of(router, channel, "chat-1")

    assert await _agent_of(SqlTopicManager(), channel, "chat-1") == "agent-y"


async def test_a_manager_reads_the_database_only_once_while_nothing_is_written(channel: str) -> None:
    manager = SqlTopicManager()
    await manager.bind_topic(channel, "chat-1", None, agent_id="agent-x")
    await _serve_from_cache(manager, channel, "chat-1")

    reads: list[None] = []
    real_get_session = connection.get_session

    def counting_get_session() -> AbstractAsyncContextManager[AsyncSession]:
        reads.append(None)
        return real_get_session()

    with patch.object(connection, "get_session", counting_get_session):
        for _ in range(5):
            assert await _agent_of(manager, channel, "chat-1") == "agent-x"

    assert reads == []


@asynccontextmanager
async def _writer_holding_the_database(channel: str) -> AsyncIterator[None]:
    """A writer with an uncommitted change that commits when the block ends; a save started meanwhile queues behind it."""
    holding, release = asyncio.Event(), asyncio.Event()

    async def write_and_hold() -> None:
        async with UnitOfWork(write=True) as uow:
            assert uow.session is not None
            uow.session.add(
                UserConfig(
                    id=uuid.uuid4().hex[:21],
                    config_key=f"hold-{channel}",
                    config_value={},
                    version="1_0",
                    last_device_id="test",
                )
            )
            holding.set()
            await release.wait()

    holder = asyncio.create_task(write_and_hold())
    await holding.wait()
    try:
        yield
    finally:
        release.set()
        await holder


async def test_a_binding_edit_waits_for_a_writer_that_holds_the_database(channel: str) -> None:
    """A save that read before it locked is aborted by the writer's commit, and a failed save is only logged."""
    async with _writer_holding_the_database(channel):
        edit = asyncio.create_task(SqlTopicManager().bind_topic(channel, "chat-1", None, agent_id="agent-x"))
        await asyncio.sleep(0.2)
    await edit

    assert await _agent_of(SqlTopicManager(), channel, "chat-1") == "agent-x"


async def test_removing_an_agent_drops_its_bindings_and_keeps_the_others(channel: str) -> None:
    gone, kept = f"gone-{channel}", f"kept-{channel}"
    manager = SqlTopicManager()
    await manager.bind_topic(channel, "chat-1", None, agent_id=gone)
    await manager.bind_topic(channel, "chat-2", None, agent_id=kept)

    await manager.remove_agent_from_all_bindings(gone)

    assert await _agent_of(SqlTopicManager(), channel, "chat-1") is None
    assert await _agent_of(SqlTopicManager(), channel, "chat-2") == kept


async def test_removing_an_agent_waits_for_a_writer_that_holds_the_database(channel: str) -> None:
    agent_id = f"agent-{channel}"
    await SqlTopicManager().bind_topic(channel, "chat-1", None, agent_id=agent_id)

    async with _writer_holding_the_database(channel):
        removal = asyncio.create_task(SqlTopicManager().remove_agent_from_all_bindings(agent_id))
        await asyncio.sleep(0.2)
    await removal

    assert await _agent_of(SqlTopicManager(), channel, "chat-1") is None
