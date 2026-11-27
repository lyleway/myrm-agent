"""Named accounts of external agents: list them with their login state, create one, remove one.

Two logins of the same CLI (a work and a personal subscription) cannot share the directory the CLI keeps its login in,
so each account is a home of its own. Creating one is refused where the host is not the user's machine, since an
account exists to hold a subscription login; listing and removing stay available so a leftover can be seen and removed.
Removing an account logs its CLI out of it first, because a login the CLI keeps outside the home (the macOS Keychain)
would be left behind, unreachable, by deleting the directory.

[INPUT]
- myrm_agent_harness.toolkits.acp.auth (POS: ACP subscription auth subsystem)
- myrm_agent_harness.toolkits.acp.core.backend_detector::BackendDetector (POS: CLI backend detection)
- app.api.external_agents._deploy_capability::require_subscription_login_capability (POS: subscription login gate)
- app.services.external_agents.accounts::account_store (POS: the store of named accounts)
- app.services.external_agents.logout::log_out_of_home (POS: logout through the CLI, shared with the logout endpoint)

[OUTPUT]
- router: account endpoints, mounted under /external-agents/accounts

[POS]
外部 Agent 命名账号 HTTP API 层：同一个 CLI 的多个订阅登录各占一个独立目录。
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.external_agents._deploy_capability import require_subscription_login_capability
from app.services.external_agents.accounts import account_store
from app.services.external_agents.logout import log_out_of_home

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.acp.auth import Account, CredentialState

router = APIRouter()


class ExternalAgentAccountRequest(BaseModel):
    backend: str = Field(..., min_length=1)
    name: str = Field(..., min_length=1)


async def _login_state(account: Account, executable: str | None) -> CredentialState:
    """What the CLI says about the login in this account's home, or what its credential file says."""
    from myrm_agent_harness.toolkits.acp.auth import CredentialStore, cli_base_env, resolve_login_state

    env = account.env(cli_base_env(executable, os.environ))
    return await resolve_login_state(CredentialStore(env), account.backend, executable=executable, env=env)


@router.get("")
async def list_external_agent_accounts() -> dict[str, list[dict[str, object]]]:
    """Every account with whether it is logged in, ordered by agent then name."""
    from myrm_agent_harness.toolkits.acp.core.backend_detector import BackendDetector

    accounts = account_store().list()
    detected = {found.name: found.path for found in await BackendDetector().detect(include_version=False)}
    states = await asyncio.gather(*(_login_state(account, detected.get(account.backend)) for account in accounts))
    return {
        "accounts": [
            {
                "backend": account.backend,
                "name": account.name,
                "authenticated": state.authenticated,
                "authStatus": state.status.value,
            }
            for account, state in zip(accounts, states, strict=True)
        ]
    }


@router.post("", status_code=201, dependencies=[Depends(require_subscription_login_capability)])
async def create_external_agent_account(body: ExternalAgentAccountRequest) -> dict[str, str]:
    """Make a new account with an empty home, to log in to afterwards."""
    from myrm_agent_harness.toolkits.acp.auth import AccountError, AccountExistsError

    try:
        account = account_store().create(body.backend, body.name)
    except AccountExistsError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"backend": account.backend, "name": account.name}


@router.delete("/{backend}/{name}")
async def delete_external_agent_account(backend: str, name: str) -> dict[str, bool]:
    """Remove an account with everything in its home, its login included.

    The CLI logs itself out of the account first. When the login still stands afterwards the account is kept (409):
    deleting its directory would leave that login behind with nothing left that could remove it.
    """
    from myrm_agent_harness.toolkits.acp.auth import AccountError, AccountNotFoundError

    store = account_store()
    try:
        account = store.get(backend, name)
        if (await log_out_of_home(backend, account.env())).authenticated:
            raise HTTPException(status_code=409, detail="The CLI could not log out of this account, so it was kept")
        await asyncio.to_thread(store.delete, backend, name)
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True}
