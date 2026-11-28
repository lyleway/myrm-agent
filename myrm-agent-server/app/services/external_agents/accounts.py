"""Where the named accounts of external agents live, and the home an agent is run in.

[INPUT]
- myrm_agent_harness.api::resolve_workspace_state_dir (POS: Single entry point for the Myrm state root; harness and server code derive every state path from it)
- myrm_agent_harness.toolkits.acp.auth::AccountStore (POS: The named accounts of an agent)
- myrm_agent_harness.toolkits.acp.drivers::command_driver (POS: The contract of every known agent)
- app.platform_utils.deployment_capabilities::get_deployment_capabilities (POS: whether the deployment keeps subscription logins)

[OUTPUT]
- account_store: The store of named accounts, under the state root.
- agent_home: The home an external agent's CLI is relocated into, or None for the CLI's own home.

[POS]
Business-layer owner of the account directory and of which home each configured external agent runs in. The harness
knows how to relocate a CLI into a home; this decides which home that is for this deployment.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from app.platform_utils.deployment_capabilities import get_deployment_capabilities

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.acp.auth import AccountStore

logger = logging.getLogger(__name__)


def account_store() -> AccountStore:
    """The named accounts. Built per call: the root follows ``MYRM_DATA_DIR`` and the store keeps no state of its own.

    The root is the one that follows a cloud sandbox's workspace volume, since that is what persists there and a CLI
    keeps its history in its home.
    """
    from myrm_agent_harness.api import resolve_workspace_state_dir
    from myrm_agent_harness.toolkits.acp.auth import AccountStore

    return AccountStore((resolve_workspace_state_dir() / "external-agents" / "accounts").resolve())


def agent_home(command: str, account: str | None) -> str | None:
    """The home ``command`` is relocated into, made when missing; ``None``: the CLI's own home.

    ``account`` is a valid account name or ``None``. A deployment that keeps no subscription logins runs every agent on
    an API key in a home that holds no login, so a login left on the disk is never read. Elsewhere an agent runs as
    the account it names, or in the CLI's own home (the login the user made on this machine) when it names none. A
    command that is not a known agent cannot be relocated.
    """
    from myrm_agent_harness.toolkits.acp.drivers import command_driver

    profile = command_driver(command)
    if profile is None:
        if account is not None:
            logger.warning("external_agent_account_ignored command=%s reason=not_a_known_agent", Path(command).name)
        return None
    store = account_store()
    if not get_deployment_capabilities().allows_subscription_login:
        return str(store.api_key_home(profile.backend))
    if account is None:
        return None
    return str(store.ensure(profile.backend, account).home)
