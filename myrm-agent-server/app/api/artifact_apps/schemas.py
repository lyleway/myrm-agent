"""Request and response models of the artifact-apps API.

[INPUT]
- app.services.artifact_apps.capabilities (POS: Capability, Decision, Outcome)
- app.services.artifact_apps.fetch_proxy (POS: Method and size limits)

[OUTPUT]
- AppSummary / AppDetail / GrantBody / GrantView / ActivityBody / ActivityView / FetchBody / FetchReply

[POS]
API layer schemas. Field limits mirror what the service enforces so a bad request fails here with a validation error.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.services.artifact_apps.capabilities import MAX_FETCH_HOSTS, Capability, Decision, Outcome
from app.services.artifact_apps.fetch_proxy import MAX_REQUEST_BYTES, MAX_URL_LENGTH, Method

_SHA256 = r"^[0-9a-f]{64}$"


class AppSummary(BaseModel):
    artifact_id: str
    name: str
    installed_at: datetime
    updated_at: datetime


class GrantView(BaseModel):
    capability: Capability
    decision: Decision
    scope: list[str]
    version_sha256: str


class VersionView(BaseModel):
    id: str
    vault_uri: str
    sha256_hash: str


class AppDetail(BaseModel):
    artifact_id: str
    name: str
    chat_id: str | None
    latest_version: VersionView
    grants: list[GrantView]


class GrantBody(BaseModel):
    decision: Decision
    scope: list[str] = Field(default_factory=list, max_length=MAX_FETCH_HOSTS)
    version_sha256: str = Field(pattern=_SHA256)


class ActivityBody(BaseModel):
    version_sha256: str = Field(pattern=_SHA256)
    capability: Capability
    outcome: Outcome
    detail: str | None = Field(default=None, max_length=255)


class ActivityView(BaseModel):
    capability: Capability
    outcome: Outcome
    detail: str | None
    version_sha256: str
    created_at: datetime


class FetchBody(BaseModel):
    version_sha256: str = Field(pattern=_SHA256)
    url: str = Field(max_length=MAX_URL_LENGTH)
    method: Method = "GET"
    headers: dict[str, str] = Field(default_factory=dict, max_length=32)
    body: str | None = Field(default=None, max_length=MAX_REQUEST_BYTES)


class FetchReply(BaseModel):
    """``ok`` is false when the proxy refused or could not complete the request; an upstream error status is ``ok``."""

    ok: bool
    status: int | None = None
    headers: dict[str, str] = Field(default_factory=dict)
    body: str = ""
    code: str | None = None
    message: str | None = None
