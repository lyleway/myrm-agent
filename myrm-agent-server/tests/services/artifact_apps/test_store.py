"""Install registry, grants and the activity log against a real schema."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models import Artifact, Base
from app.services.artifact_apps import store

_SHA_V1 = "1" * 64
_SHA_V2 = "2" * 64


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def test_installing_twice_keeps_one_row(db: AsyncSession) -> None:
    await store.install_app(db, "a1")
    await store.install_app(db, "a1")

    db.add(Artifact(id="a1", name="clock.html"))
    await db.commit()
    assert [app.artifact_id for app in await store.list_apps(db)] == ["a1"]


async def test_apps_of_deleted_artifacts_are_not_listed(db: AsyncSession) -> None:
    db.add_all([Artifact(id="live", name="live.html"), Artifact(id="gone", name="gone.html", is_deleted=True)])
    await db.commit()
    await store.install_app(db, "live")
    await store.install_app(db, "gone")

    assert [app.name for app in await store.list_apps(db)] == ["live.html"]


async def test_a_new_decision_replaces_the_previous_one(db: AsyncSession) -> None:
    await store.set_grant(db, "a1", "fetch", "allow", ["api.weather.com"], _SHA_V1)
    await store.set_grant(db, "a1", "fetch", "allow", ["api.weather.com", "b.example.org"], _SHA_V2)

    (grant,) = await store.get_grants(db, "a1")
    assert (grant.decision, store.decode_scope(grant.scope), grant.version_sha256) == (
        "allow",
        ["api.weather.com", "b.example.org"],
        _SHA_V2,
    )


async def test_only_an_allow_exposes_hosts_to_the_proxy(db: AsyncSession) -> None:
    assert await store.allowed_fetch_hosts(db, "a1") == frozenset()

    await store.set_grant(db, "a1", "fetch", "allow", ["api.weather.com"], _SHA_V1)
    assert await store.allowed_fetch_hosts(db, "a1") == {"api.weather.com"}

    await store.set_grant(db, "a1", "fetch", "deny", ["api.weather.com"], _SHA_V1)
    assert await store.allowed_fetch_hosts(db, "a1") == frozenset()


async def test_a_notify_grant_never_widens_fetch(db: AsyncSession) -> None:
    await store.set_grant(db, "a1", "notify", "allow", [], _SHA_V1)

    assert await store.allowed_fetch_hosts(db, "a1") == frozenset()


async def test_uninstalling_removes_grants_and_activity_and_reports_whether_it_was_installed(db: AsyncSession) -> None:
    await store.install_app(db, "a1")
    await store.set_grant(db, "a1", "notify", "allow", [], _SHA_V1)
    await store.record_activity(db, "a1", _SHA_V1, "notify", "ok", None)
    await store.install_app(db, "a2")
    await store.set_grant(db, "a2", "notify", "allow", [], _SHA_V1)

    assert await store.uninstall_app(db, "a1") is True
    assert await store.uninstall_app(db, "a1") is False

    assert not await store.is_installed(db, "a1")
    assert await store.get_grants(db, "a1") == []
    assert await store.list_activity(db, "a1", 10) == []
    assert len(await store.get_grants(db, "a2")) == 1


async def test_activity_is_newest_first_and_truncates_the_detail(db: AsyncSession) -> None:
    await store.record_activity(db, "a1", _SHA_V1, "fetch", "ok", "first.example.com")
    await store.record_activity(db, "a1", _SHA_V1, "fetch", "denied", "x" * 400)

    rows = await store.list_activity(db, "a1", 10)

    assert [row.outcome for row in rows] == ["denied", "ok"]
    assert rows[0].detail is not None and len(rows[0].detail) == 255


async def test_activity_keeps_only_the_newest_rows_of_each_app(db: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(store, "ACTIVITY_RETAINED_PER_APP", 3)
    for index in range(5):
        await store.record_activity(db, "a1", _SHA_V1, "fetch", "ok", f"h{index}.example.com")
    await store.record_activity(db, "a2", _SHA_V1, "fetch", "ok", "other.example.com")

    assert [row.detail for row in await store.list_activity(db, "a1", 10)] == [
        "h4.example.com",
        "h3.example.com",
        "h2.example.com",
    ]
    assert len(await store.list_activity(db, "a2", 10)) == 1
