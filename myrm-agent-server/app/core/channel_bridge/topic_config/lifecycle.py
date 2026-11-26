"""[INPUT]
- .common (POS: shared primitives of the channel topic-config domain)
- .config_store::ConfigStoreMixin (POS: reads and changes the stored topic map of a channel)
- .topic_map (POS: pure operations on the stored topic map)

[OUTPUT]
- LifecycleMixin

[POS]
Topic manager mixin: how long a binding lives (expiry, last-active bookkeeping) and the bulk unbinding of a deleted agent.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

from .common import (
    _ACTIVE_FLUSH_INTERVAL,
    logger,
)
from .config_store import ConfigStoreMixin
from .topic_map import binding_entry, drop_bindings_of


class LifecycleMixin(ConfigStoreMixin):
    # When each binding's last-active time was last written; ``SqlTopicManager.__init__`` creates it.
    _active_flush_ts: dict[str, float]

    def _is_expired(self, topic_cfg: dict[str, object]) -> bool:
        """Check if a binding has expired (idle timeout or max age)."""
        now = datetime.now(timezone.utc)

        idle_h = topic_cfg.get("idleTimeoutH")
        if isinstance(idle_h, (int, float)) and idle_h > 0:
            last_active_raw = topic_cfg.get("lastActiveAt")
            if isinstance(last_active_raw, str):
                try:
                    last_active = datetime.fromisoformat(last_active_raw)
                    if (now - last_active).total_seconds() > idle_h * 3600:
                        return True
                except (ValueError, TypeError):
                    pass

        max_age_h = topic_cfg.get("maxAgeH")
        if isinstance(max_age_h, (int, float)) and max_age_h > 0:
            bound_raw = topic_cfg.get("boundAt")
            if isinstance(bound_raw, str):
                try:
                    bound = datetime.fromisoformat(bound_raw)
                    if (now - bound).total_seconds() > max_age_h * 3600:
                        return True
                except (ValueError, TypeError):
                    pass

        return False

    async def _touch_active(self, channel: str, chat_id: str, storage_key: str) -> None:
        """Write the binding's last-active time, at most once per ``_ACTIVE_FLUSH_INTERVAL``.

        A binding removed since it was read is not brought back, and the rest of the stored map is left as it is.
        """
        mem_key = f"{channel}:{chat_id}:{storage_key}"
        now_mono = time.monotonic()
        if now_mono - self._active_flush_ts.get(mem_key, 0.0) < _ACTIVE_FLUSH_INTERVAL:
            return
        self._active_flush_ts[mem_key] = now_mono
        now_iso = datetime.now(timezone.utc).isoformat()

        def touch(config: dict[str, object]) -> bool | None:
            entry = binding_entry(config, chat_id, storage_key)
            if entry is None:
                return None
            entry["lastActiveAt"] = now_iso
            return True

        await self._mutate_config(channel, touch, best_effort=True)

    async def remove_agent_from_all_bindings(self, agent_id: str) -> None:
        """Remove an agent from all channel and topic bindings (cascading delete)."""
        try:
            channels = await self._stored_channels()
        except Exception as exc:
            logger.warning("SqlTopicManager: failed to cascade delete agent %s: %s", agent_id, exc)
            return

        for channel in channels:
            removed = await self._mutate_config(
                channel, lambda config: drop_bindings_of(config, {agent_id}) or None, best_effort=True
            )
            if removed:
                logger.info("SqlTopicManager: Cascaded deletion of agent %s from channel %s", agent_id, channel)
