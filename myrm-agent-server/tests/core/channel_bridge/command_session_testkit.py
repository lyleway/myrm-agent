"""Stand-ins for the stores ``resolve_command_session_key`` reads, so a test resolves the key the way production does.

A binding table for the topic manager, the person's settings, and the agent profiles whose own reset policy replaces the
person's; the key, and everything keyed by it, is then built by the real resolver.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.channels.types import TopicContext

_MODULE = "app.core.channel_bridge.command_session"

PERSON_KEEPS_ONE_CONVERSATION: dict[str, object] = {"sessionPolicy": {"mode": "persistent"}}
PERSON_RESETS_DAILY: dict[str, object] = {"sessionPolicy": {"mode": "daily"}}


def topic(agent_id: str | None, *, frozen: bool = False) -> TopicContext:
    return TopicContext(topic_id="t", agent_id=agent_id, identity_revoked=frozen)


class Topics:
    """``SqlTopicManager`` standing in: answers from a ``(chat_id, thread_id)`` table."""

    def __init__(self, table: dict[tuple[str, str | None], TopicContext]) -> None:
        self._table = table

    async def resolve_topic(self, channel: str, chat_id: str, thread_id: str | None) -> TopicContext | None:
        return self._table.get((chat_id, thread_id))


@contextmanager
def stubbed_stores(
    bindings: dict[tuple[str, str | None], TopicContext],
    *,
    personal: dict[str, object] | None,
    agent_policies: dict[str, dict[str, object] | None] | None = None,
) -> Iterator[AsyncMock]:
    """Stand in for the stores the key is resolved from; yields the agent-profile lookup, recorded."""
    profiles = {agent_id: SimpleNamespace(session_policy=policy) for agent_id, policy in (agent_policies or {}).items()}
    lookup = AsyncMock(side_effect=profiles.get)
    with (
        patch(f"{_MODULE}.SqlTopicManager", return_value=Topics(bindings)),
        patch(f"{_MODULE}.load_user_configs", AsyncMock(return_value=SimpleNamespace(personal_settings_dict=personal))),
        patch(f"{_MODULE}.get_agent_profile_resolver", return_value=SimpleNamespace(resolve=lookup)),
    ):
        yield lookup
