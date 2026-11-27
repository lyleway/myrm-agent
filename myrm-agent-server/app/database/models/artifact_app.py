"""Installed artifact apps: the install registry, per-capability grants and the bridge activity log.

[INPUT]
- app.database.models.base::Base (POS: SQLAlchemy Base model)

[OUTPUT]
- ArtifactApp: class — An artifact the user installed as an app.
- ArtifactAppGrant: class — The user's decision on one capability of an app.
- ArtifactAppActivity: class — Metadata-only record of one bridge call.

[POS]
Server business layer. An HTML artifact is untrusted generated code; installing it as an app is the user's opt-in to
let its page ask the host for capabilities (notifications, proxied network access, a tray item, global keys, floating). A grant records what the user
approved (decision, declared scope, the version they were looking at); the activity log records what the page then
did, as metadata only — never request bodies, response bodies or notification text.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Column, DateTime, Index, Integer, String, Text, UniqueConstraint

from app.database.models.base import Base


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class ArtifactApp(Base):
    """One row per installed artifact; uninstalling deletes the row together with its grants and activity."""

    __tablename__ = "artifact_apps"

    artifact_id = Column(String(36), primary_key=True)
    installed_at = Column(DateTime, default=_utcnow, nullable=False)


class ArtifactAppGrant(Base):
    """The latest decision on one capability.

    ``scope`` is the declared scope the user approved (a JSON list of exact host names for ``fetch``, of key
    combinations for ``shortcut``, ``[]`` for the others); a later version that declares something not covered by it is asked again. ``version_sha256`` is
    the version the user was looking at when they decided. A ``deny`` stays until the user flips it.
    """

    __tablename__ = "artifact_app_grants"
    __table_args__ = (UniqueConstraint("artifact_id", "capability", name="uq_artifact_app_grants_capability"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_id = Column(String(36), nullable=False, index=True)
    capability = Column(String(16), nullable=False)
    decision = Column(String(8), nullable=False)
    scope = Column(Text, nullable=False, default="[]")
    version_sha256 = Column(String(64), nullable=False)
    updated_at = Column(DateTime, default=_utcnow, onupdate=_utcnow, nullable=False)


class ArtifactAppActivity(Base):
    """One bridge call, successful or refused. ``detail`` is a short non-sensitive label such as a host name."""

    __tablename__ = "artifact_app_activity"
    __table_args__ = (Index("ix_artifact_app_activity_artifact_time", "artifact_id", "created_at"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    artifact_id = Column(String(36), nullable=False)
    version_sha256 = Column(String(64), nullable=False)
    capability = Column(String(16), nullable=False)
    outcome = Column(String(16), nullable=False)
    detail = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=_utcnow, nullable=False)
