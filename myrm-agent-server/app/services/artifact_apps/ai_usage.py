"""Spend ledger of the ``ai`` capability: what the apps' model calls cost, per app and per budget day.

[INPUT]
- app.database.factory::acquire_write_lock (POS: SQLite write lock taken before the first read of a transaction)
- app.database.models.artifact_app (POS: ArtifactAppAiUsage)
- app.services.budget.budget_day (POS: budget_day_start_utc, the day the guards count)

[OUTPUT]
- reserve: charge a call at its upper bound unless the app's day would exceed the cap; returns the charge id.
- settle: replace a charge by what the call actually cost.
- spent_since: money charged to one app, or to all apps, since a UTC instant.

[POS]
Business layer. The charge is written before the model is called, in the same write transaction that checks the cap, so
concurrent calls of one app cannot together pass a cap that each alone would respect, and a call that is cancelled or
fails keeps its charge. Only this module touches the ledger table.
"""

from __future__ import annotations

from datetime import datetime
from typing import Final

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models.artifact_app import ArtifactAppAiUsage
from app.services.artifact_apps.store import begin_write
from app.services.budget.budget_day import budget_day_start_utc

# Rows older than this are pruned on insert; the cap and the global budget only look at today.
USAGE_RETAINED_DAYS: Final = 8


async def reserve(db: AsyncSession, artifact_id: str, amount_usd: float, daily_cap_usd: float) -> int | None:
    """Charge ``amount_usd`` to the app unless that takes today's spend over the cap; the charge id, or None."""
    await begin_write(db)
    if await spent_since(db, budget_day_start_utc(), artifact_id) + amount_usd > daily_cap_usd:
        await db.rollback()
        return None
    charge = ArtifactAppAiUsage(artifact_id=artifact_id, cost_usd=amount_usd)
    db.add(charge)
    await db.flush()
    await db.execute(delete(ArtifactAppAiUsage).where(ArtifactAppAiUsage.created_at < budget_day_start_utc(USAGE_RETAINED_DAYS)))
    await db.commit()
    return charge.id


async def settle(db: AsyncSession, charge_id: int, actual_usd: float) -> None:
    await begin_write(db)
    charge = await db.get(ArtifactAppAiUsage, charge_id)
    if charge is not None:
        charge.cost_usd = actual_usd
    await db.commit()


async def spent_since(db: AsyncSession, since: datetime, artifact_id: str | None = None) -> float:
    """Charged since ``since``: by one app, or by all apps when ``artifact_id`` is None."""
    query = select(func.coalesce(func.sum(ArtifactAppAiUsage.cost_usd), 0.0)).where(ArtifactAppAiUsage.created_at >= since)
    if artifact_id is not None:
        query = query.where(ArtifactAppAiUsage.artifact_id == artifact_id)
    return float((await db.execute(query)).scalar_one())
