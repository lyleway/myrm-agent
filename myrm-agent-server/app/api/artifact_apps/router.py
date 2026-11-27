"""Installed artifact apps API.

[INPUT]
- app.services.artifact_apps (POS: install registry, grants, activity log, fetch and model-call proxies)
- app.services.agent.llm_access::get_llm_for_user (POS: The model the user configured)
- app.core.artifacts.listener::ensure_artifact_for_deploy (POS: Load or JIT-register the artifact)
- app.services.risk.refusal::reject_if_risky (POS: Screens the text a page sends to the model)

[OUTPUT]
- GET "": installed apps.
- PUT "/{artifact_id}" / DELETE "/{artifact_id}": install (HTML artifacts only) and uninstall.
- GET "/{artifact_id}": what the host page needs: latest version, chat, grants.
- PUT "/{artifact_id}/grants/{capability}": record the user's decision.
- GET / POST "/{artifact_id}/activity": read the log; the host reports page-side calls (notify) into it.
- POST "/{artifact_id}/fetch": the host fetch proxy.
- POST "/{artifact_id}/ai": the host model-call proxy; a prompt a risk rule holds is refused with 400 `risk_held`.

[POS]
REST API behind the app host page. The page's own code never calls it: a sandboxed page has no network, so only the
host does, on the page's behalf and only for what the user granted.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from langchain_core.language_models.chat_models import BaseChatModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_workspace_root
from app.core.artifacts.listener import ensure_artifact_for_deploy
from app.core.utils.response_utils import success_response
from app.database.connection import get_db
from app.database.models.artifact import Artifact
from app.services.agent.llm_access import get_llm_for_user
from app.services.artifact_apps import store
from app.services.artifact_apps.ai_proxy import AiRejected, AiRequest, run_ai
from app.services.artifact_apps.capabilities import Capability, InvalidScopeError, Outcome, normalize_scope
from app.services.artifact_apps.fetch_proxy import FetchRejected, FetchRequest, proxy_fetch, target_host
from app.services.artifact_apps.rate_limit import AI_LIMITER, FETCH_LIMITER
from app.services.risk.refusal import RISK_HELD_CODE, reject_if_risky

from .schemas import (
    ActivityBody,
    ActivityView,
    AiBody,
    AiReply,
    AppDetail,
    AppSummary,
    FetchBody,
    FetchReply,
    GrantBody,
    GrantView,
    VersionView,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/artifact-apps")

_HTML_SUFFIXES = (".html", ".htm")
_NOT_INSTALLED = "App not installed"
_AI_DENIED_CODES = frozenset({"permission_denied", "budget_exceeded"})


async def _installed_artifact(artifact_id: str, db: AsyncSession, workspace_root: Path) -> Artifact:
    if not await store.is_installed(db, artifact_id):
        raise HTTPException(status_code=404, detail=_NOT_INSTALLED)
    try:
        return await ensure_artifact_for_deploy(db, artifact_id, str(workspace_root))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc


def _latest_version(artifact: Artifact) -> VersionView:
    if not artifact.versions:
        raise HTTPException(status_code=404, detail="Artifact has no version")
    latest = max(artifact.versions, key=lambda version: version.created_at)
    return VersionView(id=latest.id, vault_uri=latest.vault_uri, sha256_hash=latest.sha256_hash)


async def _detail(artifact: Artifact, db: AsyncSession) -> AppDetail:
    grants = await store.get_grants(db, artifact.id)
    return AppDetail(
        artifact_id=artifact.id,
        name=artifact.name,
        chat_id=artifact.chat_id,
        latest_version=_latest_version(artifact),
        grants=[
            GrantView.model_validate(
                {
                    "capability": g.capability,
                    "decision": g.decision,
                    "scope": store.decode_scope(g.scope),
                    "version_sha256": g.version_sha256,
                }
            )
            for g in grants
        ],
    )


@router.get("")
async def list_apps(db: AsyncSession = Depends(get_db)) -> Response:
    apps = await store.list_apps(db)
    return success_response(
        [
            AppSummary(artifact_id=a.artifact_id, name=a.name, installed_at=a.installed_at, updated_at=a.updated_at).model_dump(
                mode="json"
            )
            for a in apps
        ]
    )


@router.put("/{artifact_id}")
async def install_app(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    workspace_root: Path = Depends(get_workspace_root),
) -> Response:
    try:
        artifact = await ensure_artifact_for_deploy(db, artifact_id, str(workspace_root))
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="Artifact not found") from exc
    if not artifact.name.lower().endswith(_HTML_SUFFIXES):
        raise HTTPException(status_code=400, detail="Only HTML artifacts can be installed as apps")
    await store.install_app(db, artifact.id)
    # The store begins its write in a transaction of its own, which expires what this request loaded.
    return success_response(
        (await _detail(await _installed_artifact(artifact_id, db, workspace_root), db)).model_dump(mode="json")
    )


@router.delete("/{artifact_id}", status_code=204)
async def uninstall_app(artifact_id: str, db: AsyncSession = Depends(get_db)) -> Response:
    await store.uninstall_app(db, artifact_id)
    return Response(status_code=204)


@router.get("/{artifact_id}")
async def get_app(
    artifact_id: str,
    db: AsyncSession = Depends(get_db),
    workspace_root: Path = Depends(get_workspace_root),
) -> Response:
    artifact = await _installed_artifact(artifact_id, db, workspace_root)
    return success_response((await _detail(artifact, db)).model_dump(mode="json"))


@router.put("/{artifact_id}/grants/{capability}")
async def put_grant(
    artifact_id: str,
    capability: Capability,
    body: GrantBody,
    db: AsyncSession = Depends(get_db),
) -> Response:
    if not await store.is_installed(db, artifact_id):
        raise HTTPException(status_code=404, detail=_NOT_INSTALLED)
    try:
        scope = normalize_scope(capability, body.scope)
    except InvalidScopeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    grant = await store.set_grant(db, artifact_id, capability, body.decision, scope, body.version_sha256)
    return success_response(
        GrantView(capability=capability, decision=body.decision, scope=scope, version_sha256=grant.version_sha256).model_dump(
            mode="json"
        )
    )


@router.get("/{artifact_id}/activity")
async def get_activity(
    artifact_id: str,
    limit: int = Query(default=50, ge=1, le=store.ACTIVITY_RETAINED_PER_APP),
    db: AsyncSession = Depends(get_db),
) -> Response:
    rows = await store.list_activity(db, artifact_id, limit)
    return success_response(
        [
            ActivityView.model_validate(
                {
                    "capability": r.capability,
                    "outcome": r.outcome,
                    "detail": r.detail,
                    "version_sha256": r.version_sha256,
                    "created_at": r.created_at,
                }
            ).model_dump(mode="json")
            for r in rows
        ]
    )


@router.post("/{artifact_id}/activity", status_code=204)
async def post_activity(artifact_id: str, body: ActivityBody, db: AsyncSession = Depends(get_db)) -> Response:
    if not await store.is_installed(db, artifact_id):
        raise HTTPException(status_code=404, detail=_NOT_INSTALLED)
    await store.record_activity(db, artifact_id, body.version_sha256, body.capability, body.outcome, body.detail)
    return Response(status_code=204)


@router.post("/{artifact_id}/fetch")
async def fetch_for_app(artifact_id: str, body: FetchBody, db: AsyncSession = Depends(get_db)) -> Response:
    if not await store.is_installed(db, artifact_id):
        raise HTTPException(status_code=404, detail=_NOT_INSTALLED)

    async def refuse(code: str, message: str, label: str | None) -> Response:
        outcome: Outcome = "rate_limited" if code == "rate_limited" else "denied" if code == "host_not_granted" else "error"
        await store.record_activity(db, artifact_id, body.version_sha256, "fetch", outcome, label)
        return success_response(FetchReply(ok=False, code=code, message=message).model_dump(mode="json"))

    try:
        host = target_host(body.url)
    except FetchRejected as exc:
        return await refuse(exc.code, str(exc), None)
    if not FETCH_LIMITER.try_acquire(artifact_id):
        return await refuse("rate_limited", "Too many requests from this app", host)
    try:
        result = await proxy_fetch(
            FetchRequest(body.url, body.method, body.headers, body.body),
            await store.allowed_fetch_hosts(db, artifact_id),
        )
    except FetchRejected as exc:
        return await refuse(exc.code, str(exc), host)
    finally:
        FETCH_LIMITER.release(artifact_id)
    await store.record_activity(db, artifact_id, body.version_sha256, "fetch", "ok", host)
    return success_response(
        FetchReply(ok=True, status=result.status, headers=result.headers, body=result.body).model_dump(mode="json")
    )


async def _configured_llm() -> BaseChatModel:
    try:
        return await get_llm_for_user()
    except Exception as exc:
        logger.warning("No model available for an artifact app call: %s", type(exc).__name__)
        raise AiRejected("unavailable", "No model is configured") from exc


@router.post("/{artifact_id}/ai")
async def ai_for_app(artifact_id: str, body: AiBody, db: AsyncSession = Depends(get_db)) -> Response:
    if not await store.is_installed(db, artifact_id):
        raise HTTPException(status_code=404, detail=_NOT_INSTALLED)

    async def refuse(code: str, message: str) -> Response:
        outcome: Outcome = "rate_limited" if code == "rate_limited" else "denied" if code in _AI_DENIED_CODES else "error"
        await store.record_activity(db, artifact_id, body.version_sha256, "ai", outcome, code)
        return success_response(AiReply(ok=False, code=code, message=message).model_dump(mode="json"))

    if not await store.is_allowed(db, artifact_id, "ai"):
        return await refuse("permission_denied", "The user has not allowed this app to use the model")
    if not AI_LIMITER.try_acquire(artifact_id):
        return await refuse("rate_limited", "Too many model calls from this app")
    try:
        await reject_if_risky(body.prompt, body.system)
        result = await run_ai(db, artifact_id, AiRequest(body.prompt, body.system, body.max_tokens), await _configured_llm())
    except HTTPException:
        # A risk rule holds the text: the client shows the user that refusal, and the log keeps the fact.
        await store.record_activity(db, artifact_id, body.version_sha256, "ai", "denied", RISK_HELD_CODE)
        raise
    except AiRejected as exc:
        return await refuse(exc.code, str(exc))
    finally:
        AI_LIMITER.release(artifact_id)
    await store.record_activity(db, artifact_id, body.version_sha256, "ai", "ok", f"${result.cost_usd:.4f}")
    return success_response(AiReply(ok=True, text=result.text).model_dump(mode="json"))
