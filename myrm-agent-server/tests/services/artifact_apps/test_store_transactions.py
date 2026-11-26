"""Install registry writes on the production SQLite transaction policy (WAL file, deferred read / immediate write).

The routes read before they write (is the app installed, which hosts are allowed) and the proxied request in between
takes seconds. A session that keeps that read snapshot open fails its write with SQLITE_BUSY_SNAPSHOT ("database is
locked", which busy_timeout does not cover) as soon as another request committed meanwhile: a successful proxied fetch
then answered with an error and its activity row was lost. In-memory SQLite shares one connection and cannot show this,
so these tests run on a real file with a second session committing between the caller's read and its write.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.database.factory import register_sqlite_transaction_events
from app.database.models import Base
from app.services.artifact_apps import store

_SHA = "1" * 64
_BUSY_TIMEOUT_MS = 300


def _set_sqlite_pragma(dbapi_conn: sqlite3.Connection, _record: object) -> None:
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute(f"PRAGMA busy_timeout={_BUSY_TIMEOUT_MS}")
    cursor.close()


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path / 'apps.db'}",
        connect_args={"check_same_thread": False},
        pool_size=3,
        max_overflow=2,
    )
    event.listen(engine.sync_engine, "connect", _set_sqlite_pragma)
    register_sqlite_transaction_events(engine)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def _record_activity(db: AsyncSession) -> None:
    await store.record_activity(db, "a1", _SHA, "fetch", "ok", "example.com")


async def _set_grant(db: AsyncSession) -> None:
    await store.set_grant(db, "a1", "notify", "allow", [], _SHA)


async def _install(db: AsyncSession) -> None:
    await store.install_app(db, "a2")


async def _uninstall(db: AsyncSession) -> None:
    await store.uninstall_app(db, "a1")


_WRITES: dict[str, Callable[[AsyncSession], Awaitable[None]]] = {
    "record_activity": _record_activity,
    "set_grant": _set_grant,
    "install_app": _install,
    "uninstall_app": _uninstall,
}


@pytest.mark.parametrize("name", _WRITES)
async def test_a_write_survives_a_commit_that_landed_after_the_callers_read(
    name: str, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async with sessions() as seed:
        await store.install_app(seed, "a1")

    async with sessions() as request, sessions() as other:
        assert await store.is_installed(request, "a1")  # the route's own check: the read snapshot stays open
        await store.set_grant(other, "unrelated", "notify", "allow", [], _SHA)  # another request commits meanwhile

        await _WRITES[name](request)

    async with sessions() as check:
        assert [r.outcome for r in await store.list_activity(check, "a1", 10)] == (["ok"] if name == "record_activity" else [])
        assert await store.is_installed(check, "a1") is (name != "uninstall_app")
        assert await store.is_installed(check, "a2") is (name == "install_app")
        assert {g.capability for g in await store.get_grants(check, "a1")} == ({"notify"} if name == "set_grant" else set())
