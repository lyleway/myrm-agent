"""Spend ledger of the ``ai`` capability against a real schema."""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from datetime import timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models import Base
from app.database.models.artifact_app import ArtifactAppAiUsage
from app.services.artifact_apps import ai_usage, store
from app.services.budget.budget_day import budget_day_start_utc


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


async def _spent(db: AsyncSession, artifact_id: str | None = None) -> float:
    return await ai_usage.spent_since(db, budget_day_start_utc(), artifact_id)


async def test_a_charge_that_fits_the_cap_is_written_before_the_call(db: AsyncSession) -> None:
    charge_id = await ai_usage.reserve(db, "a1", 0.4, daily_cap_usd=1.0)

    assert charge_id is not None
    assert await _spent(db, "a1") == pytest.approx(0.4)


async def test_a_charge_that_would_pass_the_cap_is_refused_and_leaves_no_row(db: AsyncSession) -> None:
    assert await ai_usage.reserve(db, "a1", 0.7, daily_cap_usd=1.0) is not None

    assert await ai_usage.reserve(db, "a1", 0.4, daily_cap_usd=1.0) is None
    assert await _spent(db, "a1") == pytest.approx(0.7)


async def test_the_cap_is_per_app(db: AsyncSession) -> None:
    assert await ai_usage.reserve(db, "a1", 0.9, daily_cap_usd=1.0) is not None

    assert await ai_usage.reserve(db, "a2", 0.9, daily_cap_usd=1.0) is not None
    assert await _spent(db) == pytest.approx(1.8)
    assert await _spent(db, "a2") == pytest.approx(0.9)


async def test_settling_replaces_the_charge_by_what_the_call_cost(db: AsyncSession) -> None:
    charge_id = await ai_usage.reserve(db, "a1", 0.9, daily_cap_usd=1.0)
    assert charge_id is not None

    await ai_usage.settle(db, charge_id, 0.01)

    assert await _spent(db, "a1") == pytest.approx(0.01)
    assert await ai_usage.reserve(db, "a1", 0.9, daily_cap_usd=1.0) is not None


async def test_yesterdays_spend_does_not_count_today(db: AsyncSession) -> None:
    db.add(ArtifactAppAiUsage(artifact_id="a1", cost_usd=5.0, created_at=budget_day_start_utc() - timedelta(hours=1)))
    await db.commit()

    assert await _spent(db, "a1") == 0.0
    assert await ai_usage.reserve(db, "a1", 0.9, daily_cap_usd=1.0) is not None


async def test_rows_older_than_the_retention_are_pruned_on_insert(db: AsyncSession) -> None:
    stale = budget_day_start_utc(ai_usage.USAGE_RETAINED_DAYS) - timedelta(hours=1)
    recent = budget_day_start_utc(1)
    db.add_all(
        [
            ArtifactAppAiUsage(artifact_id="a1", cost_usd=1.0, created_at=stale),
            ArtifactAppAiUsage(artifact_id="a1", cost_usd=2.0, created_at=recent),
        ]
    )
    await db.commit()

    await ai_usage.reserve(db, "a1", 0.1, daily_cap_usd=1.0)

    kept: Sequence[float] = (
        (await db.execute(select(ArtifactAppAiUsage.cost_usd).order_by(ArtifactAppAiUsage.cost_usd))).scalars().all()
    )
    assert kept == [0.1, 2.0]


async def test_uninstalling_keeps_the_spend_so_a_reinstall_does_not_start_the_day_again(db: AsyncSession) -> None:
    await store.install_app(db, "a1")
    await ai_usage.reserve(db, "a1", 0.9, daily_cap_usd=1.0)

    await store.uninstall_app(db, "a1")
    await store.install_app(db, "a1")

    assert await ai_usage.reserve(db, "a1", 0.2, daily_cap_usd=1.0) is None
