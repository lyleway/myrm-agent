"""Persistence for installed artifact apps.

[INPUT]
- app.database.models.artifact_app (POS: ArtifactApp, ArtifactAppGrant, ArtifactAppActivity)
- app.database.models.artifact::Artifact (POS: Joined for the app name)
- app.services.artifact_apps.capabilities (POS: Capability, Decision, Outcome)

[OUTPUT]
- install_app / uninstall_app / is_installed / list_apps: the install registry.
- get_grants / set_grant / allowed_fetch_hosts / decode_scope: per-capability decisions.
- record_activity / list_activity: the metadata-only bridge activity log.

[POS]
Business layer. The only module that reads or writes the three artifact-app tables; the API layer and the fetch proxy
go through it. Every database function takes the caller's session and commits its own write.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Final

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

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


async def install_app(db: AsyncSession, artifact_id: str) -> None:
    """Register the artifact as an app; installing twice is a no-op."""
    if await is_installed(db, artifact_id):
        return
    db.add(ArtifactApp(artifact_id=artifact_id))
    await db.commit()


async def uninstall_app(db: AsyncSession, artifact_id: str) -> bool:
    """Remove the app with its grants and activity; returns whether it was installed."""
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
