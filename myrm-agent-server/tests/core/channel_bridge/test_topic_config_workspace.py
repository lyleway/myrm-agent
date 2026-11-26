"""Topic workspace fields of SqlTopicManager: a binding points at a project or at an authorized path, never at both."""

from __future__ import annotations

from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest

from app.core.channel_bridge.topic_config import SqlTopicManager

_WORKSPACE_BIND = "app.core.channel_bridge.topic_workspace_bind"


@pytest.fixture(autouse=True)
def valid_workspaces() -> Iterator[None]:
    """Every project and path is valid: these tests are about what is stored, not about which workspaces exist."""
    with (
        patch(f"{_WORKSPACE_BIND}.assert_project_workspace", AsyncMock(return_value=None)),
        patch(f"{_WORKSPACE_BIND}.validate_authorized_path", side_effect=lambda path: path),
    ):
        yield


async def _entry(channel: str) -> dict[str, object]:
    stored = (await SqlTopicManager().get_all_topics(channel))["chat-1"]
    assert isinstance(stored, dict)
    entry = stored["__channel__"]
    assert isinstance(entry, dict)
    return entry


async def test_bind_topic_stores_project_id(topic_channel: str) -> None:
    ctx = await SqlTopicManager().bind_topic(topic_channel, "chat-1", None, project_id="proj-1")

    assert ctx.project_id == "proj-1"
    entry = await _entry(topic_channel)
    assert entry["projectId"] == "proj-1"
    assert "authorizedPath" not in entry


async def test_binding_a_path_replaces_the_project_and_the_reverse(topic_channel: str) -> None:
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, project_id="proj-1")

    on_path = await manager.bind_topic(topic_channel, "chat-1", None, authorized_path="/tmp/vault")
    assert (on_path.project_id, on_path.authorized_path) == (None, "/tmp/vault")

    on_project = await manager.bind_topic(topic_channel, "chat-1", None, project_id="proj-2")
    assert (on_project.project_id, on_project.authorized_path) == ("proj-2", None)


async def test_clearing_the_workspace_removes_both_fields(topic_channel: str) -> None:
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, project_id="proj-1")

    cleared = await manager.bind_topic(topic_channel, "chat-1", None, project_id=None, authorized_path=None)

    assert (cleared.project_id, cleared.authorized_path) == (None, None)
    entry = await _entry(topic_channel)
    assert "projectId" not in entry
    assert "authorizedPath" not in entry


async def test_a_bind_that_leaves_the_workspace_out_keeps_it(topic_channel: str) -> None:
    manager = SqlTopicManager()
    await manager.bind_topic(topic_channel, "chat-1", None, project_id="proj-1")

    kept = await manager.bind_topic(topic_channel, "chat-1", None, display_name="Team room")

    assert kept.project_id == "proj-1"


async def test_bind_topic_rejects_dual_workspace(topic_channel: str) -> None:
    with pytest.raises(ValueError, match="mutually exclusive"):
        await SqlTopicManager().bind_topic(
            topic_channel,
            "chat-1",
            None,
            project_id="proj-1",
            authorized_path="/tmp/vault",
        )

    assert await SqlTopicManager().get_all_topics(topic_channel) == {}
