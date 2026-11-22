"""[INPUT]
- .common (POS: shared primitives of the channel topic-config domain)
- app.database.factory::acquire_write_lock (POS: takes the SQLite write lock at the start of a read-then-write transaction)

[OUTPUT]
- ConfigStoreMixin: reads and writes the topic map of a channel, with a read cache per manager that any write of the process invalidates

[POS]
Topic manager mixin: UserConfig read/write for topic maps. A save holds the write lock from its read on, and every
committed save bumps the channel's write generation, which tells the read caches of the other managers they are stale.
"""

from __future__ import annotations

import time

from .common import (
    _CACHE_TTL,
    bump_write_generation,
    logger,
    write_generation,
)


class ConfigStoreMixin:
    # The read cache of the stored topic maps; ``SqlTopicManager.__init__`` creates it.
    _cache: dict[str, dict[str, object]]
    _cache_ts: dict[str, float]
    _read_generation: dict[str, int]

    async def _upsert_topic(
        self,
        channel: str,
        chat_id: str,
        storage_key: str,
        topic_entry: dict[str, object],
    ) -> None:
        config = await self._load_config(channel)

        group_topics = config.get(chat_id)
        if not isinstance(group_topics, dict):
            group_topics = {}
            config[chat_id] = group_topics

        group_topics[storage_key] = topic_entry
        await self._save_config(channel, config)

    async def _save_config(self, channel: str, config: dict[str, object]) -> None:
        try:
            from sqlalchemy import select

            from app.database.connection import get_session
            from app.database.factory import acquire_write_lock
            from app.database.models import UserConfig

            config_key = f"{channel}Topics"
            async with get_session() as session:
                # The lock comes before the read: a writer committing between this select and the update would abort
                # the save with "database is locked", and a failed save is only logged.
                await acquire_write_lock(session)
                result = await session.execute(
                    select(UserConfig).where(
                        UserConfig.config_key == config_key,
                    )
                )
                row = result.scalar_one_or_none()
                if row:
                    row.config_value = config
                else:
                    session.add(
                        UserConfig(
                            id=__import__("nanoid").generate(size=21),
                            config_key=config_key,
                            config_value=config,
                            version="1_0",
                            last_device_id="server",
                        )
                    )
                await session.commit()

            self._invalidate_cache(channel)
        except Exception as exc:
            logger.warning("SqlTopicManager: failed to save %s topics: %s", channel, exc)

    async def _load_config(self, channel: str) -> dict[str, object]:
        generation = write_generation(channel)
        if self._read_generation.setdefault(channel, generation) != generation:
            # Another manager wrote since this one cached: the copy is stale however young it is, and a save of it
            # (the last-active flush, an expiry clean-up) would undo that write.
            self._drop_cache(channel)
            self._read_generation[channel] = generation

        now = time.monotonic()
        cached_ts = self._cache_ts.get(channel, 0.0)
        if now - cached_ts < _CACHE_TTL and channel in self._cache:
            return self._cache[channel]

        try:
            from sqlalchemy import select

            from app.database.connection import get_session
            from app.database.models import UserConfig

            config_key = f"{channel}Topics"
            async with get_session() as session:
                result = await session.execute(
                    select(UserConfig.config_value).where(
                        UserConfig.config_key == config_key,
                    )
                )
                row = result.scalar_one_or_none()

            self._cache_ts[channel] = now
            data = row if isinstance(row, dict) else {}
            self._cache[channel] = data
            return data
        except Exception as exc:
            logger.warning("SqlTopicManager: failed to load %s topics: %s", channel, exc)
            return self._cache.get(channel, {})

    def _invalidate_cache(self, channel: str) -> None:
        """Called after the stored config of ``channel`` was written: drop this manager's copy, mark it stale for the rest."""
        bump_write_generation(channel)
        self._drop_cache(channel)

    def _drop_cache(self, channel: str) -> None:
        self._cache.pop(channel, None)
        self._cache_ts.pop(channel, None)
