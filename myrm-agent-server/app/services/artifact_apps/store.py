"""Persistence for installed artifact apps.

[INPUT]
- app.database.factory::acquire_write_lock (POS: SQLite write lock taken before the first read of a transaction)
- app.database.models.artifact_app (POS: ArtifactApp, ArtifactAppGrant, ArtifactAppActivity)
- app.database.models.artifact::Artifact (POS: Joined for the app name)
- app.services.artifact_apps.capabilities (POS: Capability, Decision, Outcome)

[OUTPUT]
- install_app / uninstall_app / is_installed / list_apps: the install registry.
- get_grants / set_grant / allowed_fetch_hosts / decode_scope: per-capability decisions.
- is_allowed: whether the user allowed an app a capability.
- record_activity / list_activity: the metadata-only bridge activity log.
- begin_write: the write transaction every write of the artifact-app tables starts with.

[POS]
Business layer. The only module that reads or writes the registry, grant and activity tables (the model-call spend
ledger is ai_usage's); the API layer and the proxies go through it. Every database function takes the caller's session
and commits its own write, in a transaction of its own: the caller's earlier reads (is the app installed) must not
decide whether the write can happen.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.factory import acquire_write_lock
from app.database.models.artifact import Artifact
from app.database.models.artifact_app import ArtifactApp, ArtifactAppActivity, ArtifactAppGrant
from app.services.artifact_apps.capabilities import Capability, Decision, Outcome

# Older rows are pruned on insert so the log of one app cannot grow without bound.
ACTIVITY_RETAINED_PER_APP: Final = 200


@dataclass(frozen=True, slots=True)
class InstalledApp:
    artifact_id: str
    name: str
    installed_at: datetime
    updated_at: datetime


async def begin_write(db: AsyncSession) -> None:
    """Start a write transaction that no earlier read of this session can invalidate.

    The routes read first (is the app installed, which hosts are allowed) and a proxied request can sit between that
    read and the write. A read snapshot kept open that long fails the write with SQLITE_BUSY_SNAPSHOT ("database is
    locked", which busy_timeout does not cover) once another request has committed, so the caller's read transaction is
    ended and the write lock is taken before anything is read again. The session must carry no pending changes, and
    objects the caller loaded are expired by this.
    """
    await db.rollback()
    await acquire_write_lock(db)


async def install_app(db: AsyncSession, artifact_id: str) -> None:
    """Register the artifact as an app; installing twice is a no-op."""
    await begin_write(db)
    if not await is_installed(db, artifact_id):
        db.add(ArtifactApp(artifact_id=artifact_id))
    await db.commit()


async def uninstall_app(db: AsyncSession, artifact_id: str) -> bool:
    """Remove the app with its grants and activity; returns whether it was installed.

    The model-call spend ledger stays: that money was spent, and a reinstall must not start the app's day again.
    """
    await begin_write(db)
    installed = await is_installed(db, artifact_id)
    for model in (ArtifactAppActivity, ArtifactAppGrant, ArtifactApp):
        await db.execute(delete(model).where(model.artifact_id == artifact_id))
    await db.commit()
    return installed


async def is_installed(db: AsyncSession, artifact_id: str) -> bool:
    row = await db.execute(select(ArtifactApp.artifact_id).where(ArtifactApp.artifact_id == artifact_id))
    return row.first() is not None


async def list_apps(db: AsyncSession) -> list[InstalledApp]:
    """Installed apps whose artifact still exists, newest install first."""
    rows = await db.execute(
        select(ArtifactApp, Artifact)
        .join(Artifact, Artifact.id == ArtifactApp.artifact_id)
        .where(Artifact.is_deleted.is_(False))
        .order_by(ArtifactApp.installed_at.desc())
    )
    return [InstalledApp(app.artifact_id, artifact.name, app.installed_at, artifact.updated_at) for app, artifact in rows.all()]


def decode_scope(raw: str) -> list[str]:
    scope: list[str] = json.loads(raw)
    return scope


async def get_grants(db: AsyncSession, artifact_id: str) -> list[ArtifactAppGrant]:
    rows = await db.execute(
        select(ArtifactAppGrant).where(ArtifactAppGrant.artifact_id == artifact_id).order_by(ArtifactAppGrant.capability)
    )
    return list(rows.scalars().all())


async def set_grant(
    db: AsyncSession,
    artifact_id: str,
    capability: Capability,
    decision: Decision,
    scope: list[str],
    version_sha256: str,
) -> ArtifactAppGrant:
    """Record the latest decision on one capability, replacing the previous one."""
    await begin_write(db)
    rows = await db.execute(
        select(ArtifactAppGrant).where(ArtifactAppGrant.artifact_id == artifact_id, ArtifactAppGrant.capability == capability)
    )
    grant = rows.scalars().first()
    if grant is None:
        grant = ArtifactAppGrant(artifact_id=artifact_id, capability=capability)
        db.add(grant)
    grant.decision = decision
    grant.scope = json.dumps(scope)
    grant.version_sha256 = version_sha256
    await db.commit()
    await db.refresh(grant)
    return grant


async def is_allowed(db: AsyncSession, artifact_id: str, capability: Capability) -> bool:
    rows = await db.execute(
        select(ArtifactAppGrant.decision).where(
            ArtifactAppGrant.artifact_id == artifact_id, ArtifactAppGrant.capability == capability
        )
    )
    return rows.scalars().first() == "allow"


async def allowed_fetch_hosts(db: AsyncSession, artifact_id: str) -> frozenset[str]:
    """Hosts the user allowed this app to reach; empty when the grant is missing or denied."""
    rows = await db.execute(
        select(ArtifactAppGrant).where(ArtifactAppGrant.artifact_id == artifact_id, ArtifactAppGrant.capability == "fetch")
    )
    grant = rows.scalars().first()
    if grant is None or grant.decision != "allow":
        return frozenset()
    return frozenset(decode_scope(grant.scope))


async def record_activity(
    db: AsyncSession,
    artifact_id: str,
    version_sha256: str,
    capability: Capability,
    outcome: Outcome,
    detail: str | None,
) -> None:
    await begin_write(db)
    db.add(
        ArtifactAppActivity(
            artifact_id=artifact_id,
            version_sha256=version_sha256,
            capability=capability,
            outcome=outcome,
            detail=detail[:255] if detail else None,
        )
    )
    await db.flush()
    keep = (
        select(ArtifactAppActivity.id)
        .where(ArtifactAppActivity.artifact_id == artifact_id)
        .order_by(ArtifactAppActivity.id.desc())
        .limit(ACTIVITY_RETAINED_PER_APP)
    )
    await db.execute(
        delete(ArtifactAppActivity).where(ArtifactAppActivity.artifact_id == artifact_id, ArtifactAppActivity.id.not_in(keep))
    )
    await db.commit()


async def list_activity(db: AsyncSession, artifact_id: str, limit: int) -> list[ArtifactAppActivity]:
    rows = await db.execute(
        select(ArtifactAppActivity)
        .where(ArtifactAppActivity.artifact_id == artifact_id)
        .order_by(ArtifactAppActivity.id.desc())
        .limit(limit)
    )
    return list(rows.scalars().all())
