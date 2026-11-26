"""[INPUT]
- .common (POS: shared constants and helpers)
- .config_store::ConfigStoreMixin (POS: reads and changes the stored topic map of a channel)
- .topic_map (POS: pure operations on the stored topic map)

[OUTPUT]
- PolicyMixin

[POS]
Topic manager mixin: read paths, agent-id resolution and channel-bindability checks.
"""

from __future__ import annotations

from .common import (
    SEARCH_AGENT_CHANNEL_BIND_MSG,
    logger,
)
from .config_store import ConfigStoreMixin
from .topic_map import drop_bindings_of


class PolicyMixin(ConfigStoreMixin):
    async def get_all_topics(self, channel: str) -> dict[str, object]:
        """Get all topics for a channel (used by API).

        Strips legacy Search-track bindings so Settings never shows ghost binds.
        """
        config = await self._load_config(channel)
        unbindable = await self._unbindable_agent_ids(config)
        if not unbindable:
            return config

        removed = await self._mutate_config(
            channel, lambda stored: drop_bindings_of(stored, unbindable) or None, best_effort=True
        )
        for chat_id, storage_key, agent_id in removed or []:
            logger.info(
                "SqlTopicManager: unbindable agent %s binding %s/%s/%s auto-removed",
                agent_id,
                channel,
                chat_id,
                storage_key,
            )
        return await self._load_config(channel)

    async def _resolve_agent_id(self, agent_id_or_name: str) -> str:
        """Resolve agent by UUID or name. Returns the canonical UUID.

        Raises:
            ValueError: when input is not a valid UUID and name lookup finds nothing.
        """
        import uuid as _uuid

        from app.platform_utils import get_session_factory
        from app.services.agent.agent_service import AgentService

        session_factory = get_session_factory()
        async with session_factory() as _db:
            agent = await AgentService.resolve_agent(agent_id_or_name)
            if agent:
                return str(agent.id)

        try:
            _uuid.UUID(agent_id_or_name)
        except ValueError:
            raise ValueError(f"Agent not found: '{agent_id_or_name}'") from None

        return agent_id_or_name

    async def _is_channel_bindable_agent(self, agent_id: str) -> bool:
        """Return False for Search-track or missing agents (Channel/IM = General-only)."""
        from app.services.agent.agent_service import AgentService

        agent = await AgentService.get_agent_by_id(agent_id)
        if agent is None:
            return False
        return not self._agent_is_search_track(agent)

    @staticmethod
    def _agent_is_search_track(agent: object) -> bool:
        meta = getattr(agent, "metadata", None)
        if not isinstance(meta, dict):
            return False
        return str(meta.get("prompt_mode", "full")) == "search"

    async def _unbindable_agent_ids(self, config: dict[str, object]) -> set[str]:
        """The agents the stored bindings name that a channel cannot be served by (Search-track or missing)."""
        named: set[str] = set()
        for group_topics in config.values():
            if not isinstance(group_topics, dict):
                continue
            for entry in group_topics.values():
                agent_id = entry.get("agentId") if isinstance(entry, dict) else None
                if isinstance(agent_id, str) and agent_id:
                    named.add(agent_id)
        return {agent_id for agent_id in named if not await self._is_channel_bindable_agent(agent_id)}

    async def _assert_channel_bindable_agent(self, agent_id: str) -> None:
        """Reject Search-track agents — Channel/IM always runs General CORE."""
        from app.services.agent.agent_service import AgentService

        agent = await AgentService.get_agent_by_id(agent_id)
        if agent is None:
            raise ValueError(f"Agent not found: {agent_id}")
        if self._agent_is_search_track(agent):
            raise ValueError(SEARCH_AGENT_CHANNEL_BIND_MSG)
