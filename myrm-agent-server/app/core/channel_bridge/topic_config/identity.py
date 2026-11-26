"""[INPUT]
- .common (POS: shared primitives of the channel topic-config domain)
- .config_store::ConfigStoreMixin (POS: reads and writes the stored topic map of a channel)
- app.database.factory::acquire_write_lock (POS: takes the SQLite write lock at the start of a read-then-write transaction)

[OUTPUT]
- IdentityMixin

[POS]
Topic manager mixin: identity revoke/restore, expiry and bulk unbinding.
"""

from __future__ import annotations

import copy
import time
from datetime import datetime, timezone

from .common import (
    _ACTIVE_FLUSH_INTERVAL,
    _CHANNEL_LEVEL_KEY,
    logger,
)
from .config_store import ConfigStoreMixin


class IdentityMixin(ConfigStoreMixin):
    # The last-active bookkeeping; ``SqlTopicManager.__init__`` creates it.
    _active_mem: dict[str, str]
    _active_flush_ts: dict[str, float]

    async def revoke_identity(
        self,
        channel: str,
        chat_id: str,
        thread_id: str | None,
    ) -> bool:
        """Freeze a binding's team identity without deleting stored config.

        A revoked identity routes to the default agent and stops contributing
        its memory compartment; stored memory is retained so a later rejoin
        restores continuity. Returns False when no binding exists.
        """
        config = await self._load_config(channel)
        group_topics = config.get(chat_id)
        storage_key = thread_id if thread_id is not None else _CHANNEL_LEVEL_KEY
        if not isinstance(group_topics, dict):
            return False
        topic_cfg = group_topics.get(storage_key)
        if not isinstance(topic_cfg, dict):
            return False
        topic_cfg["identityRevoked"] = True
        await self._save_config(channel, config)
        return True

    async def restore_identity(
        self,
        channel: str,
        chat_id: str,
        thread_id: str | None,
    ) -> bool:
        """Clear a previous identity revocation, restoring the binding."""
        config = await self._load_config(channel)
        group_topics = config.get(chat_id)
        storage_key = thread_id if thread_id is not None else _CHANNEL_LEVEL_KEY
        if not isinstance(group_topics, dict):
            return False
        topic_cfg = group_topics.get(storage_key)
        if not isinstance(topic_cfg, dict):
            return False
        if not topic_cfg.get("identityRevoked"):
            return False
        topic_cfg["identityRevoked"] = False
        await self._save_config(channel, config)
        return True

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

    async def _touch_active(self, channel: str, chat_id: str, storage_key: str, config: dict[str, object]) -> None:
        """Update last-active timestamp in cache and persist to DB at intervals."""
        mem_key = f"{channel}:{chat_id}:{storage_key}"
        now_mono = time.monotonic()

        last_flush = self._active_flush_ts.get(mem_key, 0.0)
        if now_mono - last_flush < _ACTIVE_FLUSH_INTERVAL:
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        self._active_mem[mem_key] = now_iso
        self._active_flush_ts[mem_key] = now_mono

        group_topics = config.get(chat_id)
        if not isinstance(group_topics, dict):
            return
        topic_cfg = group_topics.get(storage_key)
        if isinstance(topic_cfg, dict):
            topic_cfg["lastActiveAt"] = now_iso
            await self._save_config(channel, config)

    async def remove_agent_from_all_bindings(self, agent_id: str) -> None:
        """Remove an agent from all channel and topic bindings (cascading delete)."""
        try:
            from sqlalchemy import select

            from app.database.connection import get_session
            from app.database.factory import acquire_write_lock
            from app.database.models import UserConfig

            async with get_session() as session:
                # Same order as ``_save_config``: lock first, so no commit can land between the read and the update.
                await acquire_write_lock(session)
                result = await session.execute(select(UserConfig).where(UserConfig.config_key.like("%Topics")))
                rows = result.scalars().all()

                changed_channels: list[str] = []
                for row in rows:
                    if not isinstance(row.config_value, dict):
                        continue

                    # A JSON column does not track in-place edits, and assigning the same object back issues no UPDATE:
                    # the edits go to a copy.
                    config = copy.deepcopy(row.config_value)
                    changed = False
                    for chat_id, group_topics in list(config.items()):
                        if not isinstance(group_topics, dict):
                            continue

                        for storage_key, topic_cfg in list(group_topics.items()):
                            if isinstance(topic_cfg, dict) and topic_cfg.get("agentId") == agent_id:
                                # Remove the binding completely
                                del group_topics[storage_key]
                                changed = True

                        # Clean up empty chat groups
                        if not group_topics:
                            del config[chat_id]
                            changed = True

                    if changed:
                        row.config_value = config
                        session.add(row)
                        # Extract channel name from config_key (e.g., "telegramTopics" -> "telegram")
                        channel_name = row.config_key[:-6] if row.config_key.endswith("Topics") else row.config_key
                        changed_channels.append(channel_name)

                if changed_channels:
                    await session.commit()
                    for channel in changed_channels:
                        self._invalidate_cache(channel)
                        logger.info(
                            "SqlTopicManager: Cascaded deletion of agent %s from channel %s",
                            agent_id,
                            channel,
                        )

        except Exception as exc:
            logger.warning("SqlTopicManager: failed to cascade delete agent %s: %s", agent_id, exc)
