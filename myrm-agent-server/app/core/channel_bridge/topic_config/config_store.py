"""[INPUT]
- .common (POS: shared primitives of the channel topic-config domain)
- app.database.factory::acquire_write_lock (POS: takes the SQLite write lock at the start of a read-then-write transaction)

[OUTPUT]
- ConfigStoreMixin: reads the topic map of a channel through a read cache per manager, and changes it only through ``_mutate_config``

[POS]
Topic manager mixin: UserConfig read/write for topic maps. A change is applied to the stored map as it is once the write
lock is held, never to a copy read earlier, so two managers editing one channel keep each other's edits. Every committed
change bumps the channel's write generation, which tells the read caches of the other managers they are stale.
"""

from __future__ import annotations

import copy
import time
from collections.abc import Callable

from .common import (
    _CACHE_TTL,
    bump_write_generation,
    logger,
    write_generation,
)


async def _commit_mutation[T](channel: str, mutate: Callable[[dict[str, object]], T | None]) -> T | None:
    """Apply ``mutate`` to the stored topic map of ``channel`` under the write lock; ``None`` when it changed nothing."""
    from nanoid import generate as nanoid
    from sqlalchemy import select

    from app.database.connection import get_session
    from app.database.factory import acquire_write_lock
    from app.database.models import UserConfig

    config_key = f"{channel}Topics"
    async with get_session() as session:
        # The lock comes before the read: a writer committing between the select and the update would abort the save with
        # "database is locked", and the map read under the lock is the one the change is applied to.
        await acquire_write_lock(session)
        result = await session.execute(select(UserConfig).where(UserConfig.config_key == config_key))
        row = result.scalar_one_or_none()
        # A JSON column does not track in-place edits, and assigning the same object back issues no UPDATE: the edit goes
        # to a copy.
        config = copy.deepcopy(row.config_value) if row is not None and isinstance(row.config_value, dict) else {}
        outcome = mutate(config)
        if outcome is None:
            return None
        if row is None:
            session.add(
                UserConfig(
                    id=nanoid(size=21),
                    config_key=config_key,
                    config_value=config,
                    version="1_0",
                    last_device_id="server",
                )
            )
        else:
            row.config_value = config
        await session.commit()
    return outcome


class ConfigStoreMixin:
    # The read cache of the stored topic maps; ``SqlTopicManager.__init__`` creates it.
    _cache: dict[str, dict[str, object]]
    _cache_ts: dict[str, float]
    _read_generation: dict[str, int]

    async def _mutate_config[T](
        self,
        channel: str,
        mutate: Callable[[dict[str, object]], T | None],
        *,
        best_effort: bool = False,
    ) -> T | None:
        """Change the stored topic map of ``channel`` with ``mutate``, and return what ``mutate`` returned.

        ``mutate`` edits the map it is given in place and returns what it did, or ``None`` when there was nothing to change
        (nothing is written then). It runs on the map as stored once the write lock is held, so it decides from that map,
        not from what its caller read earlier, and it must not await: the lock is held until it returns. A write that fails
        raises; with ``best_effort`` (housekeeping the caller can do without) it is logged and the result is ``None``.
        """
        try:
            outcome = await _commit_mutation(channel, mutate)
        except Exception as exc:
            if not best_effort:
                raise
            logger.warning("SqlTopicManager: failed to save %s topics: %s", channel, exc)
            return None
        if outcome is not None:
            self._invalidate_cache(channel)
        return outcome

    async def _stored_channels(self) -> list[str]:
        """The channels that have a stored topic map."""
        from sqlalchemy import select

        from app.database.connection import get_session
        from app.database.models import UserConfig

        async with get_session() as session:
            result = await session.execute(select(UserConfig.config_key).where(UserConfig.config_key.like("%Topics")))
            keys = result.scalars().all()
        return [key.removesuffix("Topics") for key in keys if key.endswith("Topics")]

    async def _load_config(self, channel: str) -> dict[str, object]:
        """The topic map of ``channel`` from this manager's read cache; read it, never edit it (``_mutate_config`` edits)."""
        generation = write_generation(channel)
        if self._read_generation.setdefault(channel, generation) != generation:
            # Another manager wrote since this one cached: the copy is stale however young it is.
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
