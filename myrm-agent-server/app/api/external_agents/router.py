"""External agent subscription-auth endpoints.

GUI-driven authentication for delegated CLI agents (Codex / Claude Code /
Gemini / Qwen): query login status for badges, drive the login of an agent whose
login the host can run (over SSE, surfacing the login URL / device code), describe the
login of an agent whose login the user runs in a terminal of their own and say whether it
has been made, or log out (through the CLI itself, which alone can remove a login it keeps
outside its home). Mounted globally since subscription auth is an agent-config concern, not channel
management. The host never takes a credential: the CLI makes the login and the host only reads who
is logged in. A login is the user's own subscription on the host, so it is refused where the host is
not the user's machine (a managed sandbox runs delegated CLIs on an API key); status and logout stay
available so a leftover credential can be removed. Login, terminal login and logout act on the login in the
CLI's own home, or on the one of a named account when the request names one (the accounts themselves
are managed by ``accounts_router``, mounted under ``/accounts``).

[INPUT]
- myrm_agent_harness.toolkits.acp.auth (POS: ACP subscription auth subsystem)
- myrm_agent_harness.toolkits.acp.core.backend_detector::BackendDetector (POS: CLI backend detection)
- app.api.external_agents._deploy_capability::require_subscription_login_capability, require_backend_allowed (POS: subscription login gate, the gate of an agent the deployment does not run)
- app.api.external_agents._account_scope::account_env, resolve_account (POS: the account a request names)
- app.api.external_agents.accounts_router::router (POS: named account endpoints)
- app.services.external_agents.deployment_policy::blocked_backend (POS: whether the deployment runs an agent, for the status rows)
- app.services.external_agents.login_state::login_state, detected_executable (POS: what the CLI says about the login in one home, and which CLI that is)
- app.services.external_agents.logout::log_out_of_home (POS: logout through the CLI, shared with account removal)
- app.platform_utils.deployment_capabilities::get_deployment_capabilities (POS: deploy-mode capability flags)

[OUTPUT]
- router: external agent auth endpoints under /external-agents

[POS]
外部 Agent 订阅鉴权 HTTP API 层。让用户在自己的机器上用自有订阅驱动外部 CLI；宿主从不经手凭据；托管部署不开放登录。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from collections.abc import AsyncIterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.api.external_agents._account_scope import account_env, resolve_account
from app.api.external_agents._deploy_capability import require_backend_allowed, require_subscription_login_capability
from app.api.external_agents.accounts_router import router as accounts_router
from app.platform_utils.deployment_capabilities import get_deployment_capabilities
from app.services.external_agents.deployment_policy import blocked_backend
from app.services.external_agents.login_state import detected_executable, login_state
from app.services.external_agents.logout import log_out_of_home

logger = logging.getLogger(__name__)

router = APIRouter()
router.include_router(accounts_router, prefix="/accounts")


_ACCOUNT_DESCRIPTION = "Named account to act on; omitted: the login in the CLI's own home"


class ExternalAgentLoginRequest(BaseModel):
    command: str = Field(..., min_length=1, description="Executable command or path of the CLI")
    backend: str | None = Field(default=None, description="Backend key; inferred from command if omitted")
    timeout_seconds: int = Field(default=300, alias="timeoutSeconds", ge=30, le=1800)
    account: str | None = Field(default=None, description=_ACCOUNT_DESCRIPTION)
    remote: bool = Field(
        default=False,
        description="The browser that signs in is on another machine than this server, so the CLI is asked for a login that works from there",
    )

    class Config:
        populate_by_name = True


class ExternalAgentTerminalLoginRequest(BaseModel):
    backend: str = Field(..., min_length=1)
    account: str | None = Field(default=None, description=_ACCOUNT_DESCRIPTION)


class ExternalAgentLogoutRequest(BaseModel):
    backend: str = Field(..., min_length=1)
    account: str | None = Field(default=None, description=_ACCOUNT_DESCRIPTION)


@router.get("/auth/status")
async def external_agent_auth_status() -> dict[str, object]:
    """Report install + login state for every known backend via fresh detection.

    The login state is what each CLI says about itself where it can say it (Claude Code keeps its login in the
    macOS Keychain, which no credential-file check sees); a CLI without a status command is judged by its file.
    An agent the deployment does not run is never started: its row says it is absent and not available, and
    whether a login is left in its files.
    """
    from myrm_agent_harness.toolkits.acp.auth import CredentialStore
    from myrm_agent_harness.toolkits.acp.core.backend_detector import BackendDetector
    from myrm_agent_harness.toolkits.acp.drivers import driver_for, known_backends

    capabilities = get_deployment_capabilities()
    blocked = {name for name in known_backends() if blocked_backend(name) is not None}
    detector = BackendDetector()
    detected = {b.name: b for b in await detector.detect(include_version=True, refresh=True)}
    # The login of an agent the deployment does not run is only read from its files, so that a login left there can still
    # be seen and removed.
    probed = [name for name in known_backends() if name not in blocked]
    states = dict(
        zip(
            probed,
            await asyncio.gather(*(login_state(name, detected[name].path if name in detected else None) for name in probed)),
            strict=True,
        )
    )
    store = CredentialStore()

    backends: list[dict[str, object]] = []
    for name in known_backends():
        profile = driver_for(name)
        if profile is None:
            continue
        available = name not in blocked
        found = detected.get(name) if available else None
        state = states[name] if available else store.state(name)
        backends.append(
            {
                "backend": name,
                "installed": found is not None,
                "path": found.path if found else None,
                "version": found.version if found else None,
                "authenticated": state.authenticated,
                "authStatus": state.status.value,
                "transport": profile.transport,
                "loginStrategy": profile.login_strategy.value,
                "subscriptionLoginAllowed": capabilities.allows_subscription_login,
                "availableInDeployment": available,
            }
        )
    return {"backends": backends}


@router.post("/install/{backend}", dependencies=[Depends(require_backend_allowed)])
async def external_agent_install(backend: str) -> StreamingResponse:
    """Install an external agent CLI into the isolated toolchain.

    ``success`` is sent only after the toolchain has seen the installed CLI start; any other ending is an ``error``.
    An agent the deployment does not run (``blocked_external_backends``) is refused before anything is downloaded.
    """
    from myrm_agent_harness.toolkits.acp.core.backend_detector import BackendDetector
    from myrm_agent_harness.toolkits.acp.toolchains import IsolatedToolchainManager, ToolchainError

    manager = IsolatedToolchainManager()

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for msg in manager.install_backend(backend):
                yield f"data: {json.dumps({'type': 'progress', 'message': msg})}\n\n"
            yield f"data: {json.dumps({'type': 'success', 'message': 'Installation complete'})}\n\n"
        except ToolchainError as exc:
            logger.warning("external_agent_install_failed backend=%s reason=%s", backend, exc)
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        except Exception as exc:
            logger.error("external_agent_install_crashed backend=%s", backend, exc_info=True)
            yield f"data: {json.dumps({'type': 'error', 'message': f'Installation failed: {exc}'})}\n\n"
        finally:
            BackendDetector().invalidate_cache()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/auth/login", dependencies=[Depends(require_subscription_login_capability)])
async def external_agent_auth_login(
    body: ExternalAgentLoginRequest,
) -> StreamingResponse:
    """Drive the CLI login of an agent whose login the host can run, streaming progress events as SSE.

    The stream ends when the login does, and closing it stops the CLI. An agent whose login runs in a terminal is
    refused (400): ask for its terminal login.
    """
    from myrm_agent_harness.toolkits.acp.auth import CliLoginSession
    from myrm_agent_harness.toolkits.acp.drivers import LoginStrategy, driver_for

    profile = driver_for(body.backend or body.command)
    if profile is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown external agent backend: {body.backend or body.command}",
        )
    if profile.login_strategy is not LoginStrategy.BROWSER_OAUTH:
        raise HTTPException(status_code=400, detail=f"{profile.backend} login runs in a terminal; it is not started here")

    session = CliLoginSession(
        body.command,
        profile,
        base_env=account_env(profile.backend, body.account, create=True),
        timeout_seconds=body.timeout_seconds,
        remote=body.remote,
    )

    async def event_stream() -> AsyncIterator[str]:
        try:
            async for event in session.run():
                payload: dict[str, object] = {
                    "type": event.type.value,
                    "message": event.message,
                }
                if event.url:
                    payload["url"] = event.url
                if event.code:
                    payload["code"] = event.code
                yield f"data: {json.dumps(payload)}\n\n"
        except Exception as exc:  # pragma: no cover - defensive: never leak a raw 500 into the stream
            logger.warning(
                "external_agent_login_stream_error backend=%s error=%s",
                profile.backend,
                exc,
            )
            yield f"data: {json.dumps({'type': 'error', 'message': str(exc)})}\n\n"
        finally:
            await session.cancel()

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/auth/terminal-login", dependencies=[Depends(require_subscription_login_capability)])
async def external_agent_terminal_login(body: ExternalAgentTerminalLoginRequest) -> dict[str, object]:
    """The login the user runs in a terminal of their own, spelled for each shell, and whether it has been made yet.

    The CLI runs there, so no credential passes through this server. Asking again is how the user learns that the
    login finished: ``authenticated`` is what the CLI itself reports about the login in that home. The account the
    request names is made first when the agent has none of that name.
    """
    from myrm_agent_harness.toolkits.acp.auth import default_shell, terminal_login
    from myrm_agent_harness.toolkits.acp.drivers import driver_for

    profile = driver_for(body.backend)
    if profile is None:
        raise HTTPException(status_code=400, detail=f"Unknown external agent backend: {body.backend}")
    executable = await detected_executable(profile.backend)
    if executable is None:
        raise HTTPException(status_code=409, detail=f"{profile.backend} is not installed")
    account = resolve_account(profile.backend, body.account, create=True)
    state = await login_state(profile.backend, executable, account)
    login = terminal_login(profile, executable, account_home=account.home if account is not None else None)
    return {
        "backend": profile.backend,
        "account": body.account,
        "authenticated": state.authenticated,
        "authStatus": state.status.value,
        "defaultShell": default_shell(),
        "commands": login.commands(),
    }


@router.post("/auth/logout")
async def external_agent_auth_logout(
    body: ExternalAgentLogoutRequest,
) -> dict[str, object]:
    """Log a backend out of its login (the named account's, when one is named) and remove its stored credentials.

    The CLI is asked to log itself out first: a login it keeps outside its home survives the removal of files. The
    answer is what the CLI reports afterwards, so a login that could not be removed is still reported as one.
    """
    env = account_env(body.backend, body.account, create=False)
    try:
        state = await log_out_of_home(body.backend, env if env is not None else os.environ)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {
        "backend": state.backend,
        "authenticated": state.authenticated,
        "authStatus": state.status.value,
    }
