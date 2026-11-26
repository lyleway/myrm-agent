"""A topic binding survives what runs beside it: edits through other managers, a writer that holds the database,
and housekeeping that judged a binding before it was rebound or removed."""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.types import ReplyMode
from app.core.channel_bridge.topic_config import SqlTopicManager, config_store
from app.core.channel_bridge.topic_config.common import write_generation
from app.database import connection
from app.database.models import UserConfig
from app.database.repositories.uow import UnitOfWork

_FLUSH_INTERVAL = "app.core.channel_bridge.topic_config.lifecycle._ACTIVE_FLUSH_INTERVAL"


@pytest.fixture(autouse=True)
def bindable_agents() -> Iterator[None]:
    """Any agent id binds: these tests are about storage, not about which agents exist."""
    with (
        patch.object(SqlTopicManager, "_resolve_agent_id", AsyncMock(side_effect=lambda agent_id: agent_id)),
        patch.object(SqlTopicManager, "_assert_channel_bindable_agent", AsyncMock(return_value=None)),
        patch.object(SqlTopicManager, "_is_channel_bindable_agent", AsyncMock(return_value=True)),
    ):
        yield


async def _agent_of(manager: SqlTopicManager, channel: str, chat_id: str) -> str | None:
    topic = await manager.resolve_topic(channel, chat_id, None)
    return topic.agent_id if topic else None


async def _stored_chats(channel: str) -> list[str]:
    return sorted(await SqlTopicManager().get_all_topics(channel))


async def _serve_from_cache(manager: SqlTopicManager, channel: str, chat_id: str) -> None:
    """Resolve until the manager answers from its cache: the first resolutions still flush the last-active time."""
    for _ in range(3):
        await _agent_of(manager, channel, chat_id)


@contextmanager
def _counting_commits() -> Iterator[list[str]]:
    """The channels committed to inside the block, one entry per attempt to change the stored topic config."""
    real_commit = config_store._commit_mutation
    attempts: list[str] = []

    async def counting_commit[T](channel: str, mutate: Callable[[dict[str, object]], T | None]) -> T | None:
        attempts.append(channel)
        return await real_commit(channel, mutate)

    with patch.object(config_store, "_commit_mutation", counting_commit):
        yield attempts


def _failing_commit() -> AsyncMock:
    return AsyncMock(side_effect=RuntimeError("database is locked"))


async def test_a_manager_serves_a_binding_edited_through_another_manager_at_once(topic_channel: str) -> None:
    router, api = SqlTopicManager(), SqlTopicManager()
    await api.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x")
    await _serve_from_cache(router, topic_channel, "chat-1")
    assert await _agent_of(router, topic_channel, "chat-1") == "agent-x"

    await api.bind_topic(topic_channel, "chat-1", None, agent_id="agent-y")

    assert await _agent_of(router, topic_channel, "chat-1") == "agent-y"


async def test_the_last_active_flush_of_a_stale_manager_does_not_undo_an_edit(topic_channel: str) -> None:
    router, api = SqlTopicManager(), SqlTopicManager()
    await api.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x")
    await _serve_from_cache(router, topic_channel, "chat-1")
    await api.bind_topic(topic_channel, "chat-1", None, agent_id="agent-y")

    with patch(_FLUSH_INTERVAL, 0.0):
        await _agent_of(router, topic_channel, "chat-1")

    assert await _agent_of(SqlTopicManager(), topic_channel, "chat-1") == "agent-y"


async def test_a_manager_reads_the_database_only_once_while_nothing_is_written(topic_channel: str) -> None:
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x")
    await _serve_from_cache(manager, topic_channel, "chat-1")

    reads: list[None] = []
    real_get_session = connection.get_session

    def counting_get_session() -> AbstractAsyncContextManager[AsyncSession]:
        reads.append(None)
        return real_get_session()

    with patch.object(connection, "get_session", counting_get_session):
        for _ in range(5):
            assert await _agent_of(manager, topic_channel, "chat-1") == "agent-x"

    assert reads == []


async def test_bindings_made_at_the_same_time_through_different_managers_all_survive(topic_channel: str) -> None:
    """Each edit lands on the config as stored when its turn comes, so no manager overwrites another's."""
    managers = [SqlTopicManager() for _ in range(8)]

    await asyncio.gather(
        *(manager.bind_topic(topic_channel, f"chat-{i}", None, agent_id=f"agent-{i}") for i, manager in enumerate(managers))
    )

    assert await _stored_chats(topic_channel) == sorted(f"chat-{i}" for i in range(8))


async def test_edits_of_one_binding_made_at_the_same_time_each_keep_their_own_fields(topic_channel: str) -> None:
    first, second = SqlTopicManager(), SqlTopicManager()

    await asyncio.gather(
        first.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x"),
        second.bind_topic(topic_channel, "chat-1", None, reply_mode=ReplyMode.DRAFT_REVIEW),
    )

    topic = await SqlTopicManager().resolve_topic(topic_channel, "chat-1", None)
    assert topic is not None
    assert (topic.agent_id, topic.reply_mode) == ("agent-x", ReplyMode.DRAFT_REVIEW)


async def test_a_bind_keeps_the_fields_it_does_not_set(topic_channel: str) -> None:
    manager = SqlTopicManager()
    first = await manager.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x", reply_mode=ReplyMode.DRAFT_REVIEW)

    second = await manager.bind_topic(topic_channel, "chat-1", None, display_name="Team room")

    assert (second.agent_id, second.reply_mode, second.bound_at) == ("agent-x", ReplyMode.DRAFT_REVIEW, first.bound_at)
    entry = (await SqlTopicManager().get_all_topics(topic_channel))["chat-1"]
    assert isinstance(entry, dict)
    assert entry["__channel__"]["displayName"] == "Team room"


async def test_a_bind_that_cannot_be_saved_fails_instead_of_reporting_success(topic_channel: str) -> None:
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x")

    with patch.object(config_store, "_commit_mutation", _failing_commit()):
        with pytest.raises(RuntimeError, match="database is locked"):
            await manager.bind_topic(topic_channel, "chat-2", None, agent_id="agent-y")
        with pytest.raises(RuntimeError, match="database is locked"):
            await manager.unbind_topic(topic_channel, "chat-1", None)

    assert await _stored_chats(topic_channel) == ["chat-1"]


async def test_unbinding_reports_whether_a_binding_was_removed(topic_channel: str) -> None:
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x")

    assert await manager.unbind_topic(topic_channel, "chat-1", None) is True
    assert await manager.unbind_topic(topic_channel, "chat-1", None) is False
    assert await _stored_chats(topic_channel) == []


async def test_a_change_that_finds_nothing_to_do_stores_nothing(topic_channel: str) -> None:
    assert await SqlTopicManager().unbind_topic(topic_channel, "chat-1", None) is False

    async with connection.get_session() as session:
        stored = await session.execute(select(UserConfig.id).where(UserConfig.config_key == f"{topic_channel}Topics"))
        assert stored.first() is None


async def test_a_change_that_finds_nothing_to_do_leaves_the_read_caches_valid(topic_channel: str) -> None:
    router, api = SqlTopicManager(), SqlTopicManager()
    await api.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x")
    await _serve_from_cache(router, topic_channel, "chat-1")
    generation = write_generation(topic_channel)

    assert await api.unbind_topic(topic_channel, "chat-2", None) is False

    assert write_generation(topic_channel) == generation


async def test_metadata_is_written_only_when_the_platform_reports_something_new(topic_channel: str) -> None:
    manager = SqlTopicManager()

    with _counting_commits() as commits:
        await manager.sync_topic_metadata(topic_channel, "chat-1", None, display_name="Team", avatar_url="a.png")
        assert len(commits) == 1
        await manager.sync_topic_metadata(topic_channel, "chat-1", None, display_name="Team", avatar_url="a.png")
        assert len(commits) == 1
        await manager.sync_topic_metadata(topic_channel, "chat-1", None, display_name="Team 2")
        assert len(commits) == 2

    entry = (await SqlTopicManager().get_all_topics(topic_channel))["chat-1"]
    assert isinstance(entry, dict)
    assert (entry["__channel__"]["displayName"], entry["__channel__"]["avatarUrl"]) == ("Team 2", "a.png")


async def test_the_same_metadata_reported_to_two_managers_at_once_is_written_once(topic_channel: str) -> None:
    """Both managers saw nothing stored when they decided to write; the one that gets the lock second finds it already there."""
    first, second = SqlTopicManager(), SqlTopicManager()
    both_decided = asyncio.Barrier(2)
    real_load_config = SqlTopicManager._load_config

    async def load_then_wait_for_the_other(self: SqlTopicManager, channel: str) -> dict[str, object]:
        config = await real_load_config(self, channel)
        await both_decided.wait()
        return config

    generation = write_generation(topic_channel)
    with patch.object(SqlTopicManager, "_load_config", load_then_wait_for_the_other):
        await asyncio.gather(
            first.sync_topic_metadata(topic_channel, "chat-1", None, display_name="Team"),
            second.sync_topic_metadata(topic_channel, "chat-1", None, display_name="Team"),
        )

    assert write_generation(topic_channel) == generation + 1
    entry = (await SqlTopicManager().get_all_topics(topic_channel))["chat-1"]
    assert isinstance(entry, dict)
    assert entry["__channel__"]["displayName"] == "Team"


async def test_metadata_of_a_bound_chat_leaves_its_binding_alone(topic_channel: str) -> None:
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x")

    await manager.sync_topic_metadata(topic_channel, "chat-1", None, display_name="Team")

    assert await _agent_of(SqlTopicManager(), topic_channel, "chat-1") == "agent-x"


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


async def test_a_binding_edit_waits_for_a_writer_that_holds_the_database(topic_channel: str) -> None:
    """A save that read before it locked is aborted by the writer's commit; a save that locks first just waits."""
    async with _writer_holding_the_database(topic_channel):
        edit = asyncio.create_task(SqlTopicManager().bind_topic(topic_channel, "chat-1", None, agent_id="agent-x"))
        await asyncio.sleep(0.2)
    await edit

    assert await _agent_of(SqlTopicManager(), topic_channel, "chat-1") == "agent-x"


async def test_removing_an_agent_drops_its_bindings_and_keeps_the_others(topic_channel: str) -> None:
    gone, kept = f"gone-{topic_channel}", f"kept-{topic_channel}"
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id=gone)
    await manager.bind_topic(topic_channel, "chat-2", None, agent_id=kept)

    await manager.remove_agent_from_all_bindings(gone)

    assert await _agent_of(SqlTopicManager(), topic_channel, "chat-1") is None
    assert await _agent_of(SqlTopicManager(), topic_channel, "chat-2") == kept


async def test_removing_an_agent_clears_its_bindings_in_every_channel(topic_channel: str) -> None:
    other_channel = f"{topic_channel}-other"
    agent_id = f"agent-{topic_channel}"
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id=agent_id)
    await manager.bind_topic(other_channel, "chat-2", None, agent_id=agent_id)

    await manager.remove_agent_from_all_bindings(agent_id)

    assert await _agent_of(SqlTopicManager(), topic_channel, "chat-1") is None
    assert await _agent_of(SqlTopicManager(), other_channel, "chat-2") is None


async def test_removing_an_agent_waits_for_a_writer_that_holds_the_database(topic_channel: str) -> None:
    agent_id = f"agent-{topic_channel}"
    await SqlTopicManager().bind_topic(topic_channel, "chat-1", None, agent_id=agent_id)

    async with _writer_holding_the_database(topic_channel):
        removal = asyncio.create_task(SqlTopicManager().remove_agent_from_all_bindings(agent_id))
        await asyncio.sleep(0.2)
    await removal

    assert await _agent_of(SqlTopicManager(), topic_channel, "chat-1") is None


async def test_housekeeping_that_cannot_be_saved_is_logged_and_the_message_still_resolves(
    topic_channel: str, caplog: pytest.LogCaptureFixture
) -> None:
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x")
    failing = _failing_commit()

    with (
        caplog.at_level(logging.WARNING),
        patch.object(SqlTopicManager, "_is_expired", lambda self, entry: True),
        patch.object(config_store, "_commit_mutation", failing),
    ):
        assert await _agent_of(manager, topic_channel, "chat-1") is None

    assert failing.await_count == 1
    assert "failed to save" in caplog.text
    assert await _stored_chats(topic_channel) == ["chat-1"]


async def test_an_expired_binding_is_removed_when_it_is_read(topic_channel: str) -> None:
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x")

    with patch.object(SqlTopicManager, "_is_expired", lambda self, entry: True):
        assert await _agent_of(manager, topic_channel, "chat-1") is None

    assert await _stored_chats(topic_channel) == []


async def test_a_binding_rebound_while_it_was_judged_unbindable_is_kept(topic_channel: str) -> None:
    """The removal revalidates against the stored binding: the judgement was about the copy read earlier."""
    router, api = SqlTopicManager(), SqlTopicManager()
    await api.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x")

    async def judge_while_rebound(self: SqlTopicManager, agent_id: str) -> bool:
        await api.bind_topic(topic_channel, "chat-1", None, agent_id="agent-y")
        return False

    with patch.object(SqlTopicManager, "_is_channel_bindable_agent", judge_while_rebound):
        assert await _agent_of(router, topic_channel, "chat-1") is None

    assert await _agent_of(SqlTopicManager(), topic_channel, "chat-1") == "agent-y"


async def test_the_last_active_flush_does_not_bring_back_a_binding_removed_meanwhile(topic_channel: str) -> None:
    router, api = SqlTopicManager(), SqlTopicManager()
    await api.bind_topic(topic_channel, "chat-1", None, agent_id="agent-x")

    async def judge_while_unbound(self: SqlTopicManager, agent_id: str) -> bool:
        await api.unbind_topic(topic_channel, "chat-1", None)
        return True

    with patch(_FLUSH_INTERVAL, 0.0), patch.object(SqlTopicManager, "_is_channel_bindable_agent", judge_while_unbound):
        assert await _agent_of(router, topic_channel, "chat-1") == "agent-x"

    assert await _stored_chats(topic_channel) == []
