"""The account an external-agent auth request acts on.

Login, credential import and logout each act on one login of one CLI: the one in the CLI's own home, or the one in a
named account's home. This resolves which, so the three entry points answer unusable names the same way. An account
exists once something uses it, so a login or an import into one the machine does not have yet (a setting synced from
another machine names it) makes it first; a logout never does, since there is nothing to log out of.

[INPUT]
- app.services.external_agents.accounts::account_store (POS: the store of named accounts)
- myrm_agent_harness.toolkits.acp.auth::AccountError, AccountNotFoundError (POS: why an account cannot be found)

[OUTPUT]
- account_env: The environment that reaches an account's home, or None for the CLI's own home.

[POS]
HTTP-facing resolver of the account an auth request names; turns the store's errors into status codes.
"""

from __future__ import annotations

from fastapi import HTTPException

from app.services.external_agents.accounts import account_store


def account_env(backend: str, account: str | None, *, create: bool) -> dict[str, str] | None:
    """The environment that reaches ``account``'s home, or ``None`` when no account is named (the CLI's own home).

    ``create``: make the account when the agent has none of that name, instead of answering 404.

    Raises:
        HTTPException: 404 when ``create`` is false and the agent has no such account, 400 when the agent or the
            name is not a valid one.
    """
    from myrm_agent_harness.toolkits.acp.auth import AccountError, AccountNotFoundError

    if account is None:
        return None
    try:
        store = account_store()
        return (store.ensure(backend, account) if create else store.get(backend, account)).env()
    except AccountNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except AccountError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
