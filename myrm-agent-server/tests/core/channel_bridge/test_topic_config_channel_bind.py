"""Channel bind policy of SqlTopicManager: a channel is served by General agents that exist, never by Search-track ones."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.channel_bridge.topic_config import (
    SEARCH_AGENT_CHANNEL_BIND_MSG,
    SqlTopicManager,
    config_store,
    is_search_agent_channel_bind_error,
)


def _agent(prompt_mode: str) -> MagicMock:
    return MagicMock(metadata={"prompt_mode": prompt_mode})


@pytest.fixture
def agents() -> Iterator[dict[str, MagicMock]]:
    """The agents the service knows, by id; a test edits the registry to change what an agent is."""
    registry: dict[str, MagicMock] = {}

    async def get_agent_by_id(agent_id: str) -> MagicMock | None:
        return registry.get(agent_id)

    with (
        patch("app.services.agent.agent_service.AgentService.get_agent_by_id", AsyncMock(side_effect=get_agent_by_id)),
        patch.object(SqlTopicManager, "_resolve_agent_id", AsyncMock(side_effect=lambda agent_id: agent_id)),
    ):
        yield registry


async def _stored_chats(channel: str) -> list[str]:
    return sorted(await SqlTopicManager().get_all_topics(channel))


def test_is_search_agent_channel_bind_error() -> None:
    assert is_search_agent_channel_bind_error(ValueError(SEARCH_AGENT_CHANNEL_BIND_MSG))
    assert not is_search_agent_channel_bind_error(ValueError("Agent not found: x"))


@pytest.mark.parametrize("agent_id", ["builtin-fast-search", "builtin-deep-search"])
async def test_bind_topic_rejects_search_agent(topic_channel: str, agents: dict[str, MagicMock], agent_id: str) -> None:
    agents[agent_id] = _agent("search")

    with pytest.raises(ValueError, match=SEARCH_AGENT_CHANNEL_BIND_MSG):
        await SqlTopicManager().bind_topic(topic_channel, "chat-1", None, agent_id=agent_id)

    assert await _stored_chats(topic_channel) == []


async def test_bind_topic_allows_general_agent(topic_channel: str, agents: dict[str, MagicMock]) -> None:
    agents["general-1"] = _agent("full")

    ctx = await SqlTopicManager().bind_topic(topic_channel, "chat-1", None, agent_id="general-1")

    assert ctx.agent_id == "general-1"
    assert await _stored_chats(topic_channel) == ["chat-1"]


async def test_bind_topic_rejects_missing_agent(topic_channel: str, agents: dict[str, MagicMock]) -> None:
    with pytest.raises(ValueError, match="Agent not found: missing-agent"):
        await SqlTopicManager().bind_topic(topic_channel, "chat-1", None, agent_id="missing-agent")

    assert await _stored_chats(topic_channel) == []


async def test_resolve_topic_purges_a_binding_whose_agent_became_search_track(
    topic_channel: str, agents: dict[str, MagicMock]
) -> None:
    agents["agent-1"] = _agent("full")
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id="agent-1")

    agents["agent-1"] = _agent("search")
    assert await manager.resolve_topic(topic_channel, "chat-1", None) is None

    # Gone for good, not just hidden while the agent is a Search-track one.
    agents["agent-1"] = _agent("full")
    assert await SqlTopicManager().resolve_topic(topic_channel, "chat-1", None) is None


async def test_resolve_topic_purges_a_binding_whose_agent_was_deleted(topic_channel: str, agents: dict[str, MagicMock]) -> None:
    agents["agent-1"] = _agent("full")
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id="agent-1")

    del agents["agent-1"]
    assert await manager.resolve_topic(topic_channel, "chat-1", None) is None

    agents["agent-1"] = _agent("full")
    assert await SqlTopicManager().resolve_topic(topic_channel, "chat-1", None) is None


async def test_resolve_topic_keeps_general_agent_binding(topic_channel: str, agents: dict[str, MagicMock]) -> None:
    agents["general-1"] = _agent("full")
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id="general-1")

    ctx = await manager.resolve_topic(topic_channel, "chat-1", None)

    assert ctx is not None
    assert ctx.agent_id == "general-1"
    assert await _stored_chats(topic_channel) == ["chat-1"]


async def test_get_all_topics_strips_search_bindings(topic_channel: str, agents: dict[str, MagicMock]) -> None:
    agents["agent-a"] = _agent("full")
    agents["agent-b"] = _agent("full")
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id="agent-a")
    await manager.bind_topic(topic_channel, "chat-2", None, agent_id="agent-b")

    agents["agent-a"] = _agent("search")
    assert sorted(await manager.get_all_topics(topic_channel)) == ["chat-2"]

    agents["agent-a"] = _agent("full")
    assert await _stored_chats(topic_channel) == ["chat-2"]


async def test_get_all_topics_still_answers_when_the_cleanup_cannot_be_saved(
    topic_channel: str, agents: dict[str, MagicMock]
) -> None:
    agents["agent-a"] = _agent("full")
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, agent_id="agent-a")

    agents["agent-a"] = _agent("search")
    with patch.object(config_store, "_commit_mutation", AsyncMock(side_effect=RuntimeError("database is locked"))):
        assert sorted(await manager.get_all_topics(topic_channel)) == ["chat-1"]


def test_agent_is_search_track_metadata_edges() -> None:
    assert not SqlTopicManager._agent_is_search_track(MagicMock(metadata=None))
    assert not SqlTopicManager._agent_is_search_track(MagicMock(metadata="not-a-dict"))
    assert not SqlTopicManager._agent_is_search_track(MagicMock(metadata={"prompt_mode": "full"}))
    assert SqlTopicManager._agent_is_search_track(MagicMock(metadata={"prompt_mode": "search"}))


async def test_unbindable_agent_ids_skips_malformed_binding_entries() -> None:
    config: dict[str, object] = {
        "bad-group": "not-a-dict",
        "chat-ok": {
            "bad-topic": "not-a-dict",
            "__channel__": {"agentId": "", "enabled": True},
        },
    }

    with patch(
        "app.services.agent.agent_service.AgentService.get_agent_by_id",
        new_callable=AsyncMock,
    ) as get_agent_by_id:
        unbindable = await SqlTopicManager()._unbindable_agent_ids(config)

    assert unbindable == set()
    get_agent_by_id.assert_not_called()
