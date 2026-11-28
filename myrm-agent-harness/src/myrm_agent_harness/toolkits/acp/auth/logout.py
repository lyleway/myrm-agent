"""Log a CLI out the way the CLI does it: the only thing that knows where its login is kept.

Deleting the credential files cannot remove a login kept elsewhere (Claude Code on macOS keeps it in the Keychain, under
a name derived from the home it is relocated into), so a file-only logout leaves the CLI logged in and an account removed
by deleting its directory leaves its secret behind.

[INPUT]
- toolkits.acp.drivers::driver_for (POS: The contract of every known agent: its login, launch, transport and permission facts)
- toolkits.acp.auth.credential_store::CredentialState, CredentialStore (POS: Credential persistence and detection.)
- toolkits.acp.auth.launch_env::cli_base_env (POS: The environment every launch of a CLI agent starts from)
- toolkits.acp.auth.login_state::resolve_login_state (POS: The login state a CLI reports about itself)
- toolkits.acp.core.probe::run_probe (POS: Process primitive of the detection and login-state checks)

[OUTPUT]
- log_out: Remove the login of a backend and return the state its CLI reports afterwards.

[POS]
Logout of the ACP auth subsystem; the counterpart of the interactive login session.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping

from myrm_agent_harness.toolkits.acp.auth.credential_store import CredentialState, CredentialStore
from myrm_agent_harness.toolkits.acp.auth.launch_env import cli_base_env
from myrm_agent_harness.toolkits.acp.auth.login_state import resolve_login_state
from myrm_agent_harness.toolkits.acp.core.probe import run_probe
from myrm_agent_harness.toolkits.acp.drivers import driver_for

logger = logging.getLogger(__name__)


async def log_out(
    store: CredentialStore,
    backend: str,
    *,
    executable: str | None,
    env: Mapping[str, str],
    timeout_seconds: float = 15.0,
) -> CredentialState:
    """Remove the login of ``backend``, and return the state its CLI reports afterwards.

    The CLI is asked to log itself out first (``DriverSpec.logout_args``), then the credential files are removed: they
    hold the logins the CLI does not own (an imported file) and the whole login of a CLI that has no logout command. A
    CLI that fails to log out does not stop the files from going; the returned state tells whether a login remains.
    ``env`` is what the CLI runs with, so it acts on the login of the home it is relocated into. ``executable``: the CLI
    is not installed when ``None``, and only the files are removed.

    Raises:
        ValueError: ``backend`` has no auth profile.
    """
    profile = driver_for(backend)
    if profile is None:
        msg = f"No auth profile for backend {backend!r}"
        raise ValueError(msg)
    cli_env = cli_base_env(executable, env)
    if executable is not None and profile.logout_args:
        done = await run_probe([executable, *profile.logout_args], env=cli_env, timeout_seconds=timeout_seconds)
        if done is None or done.returncode != 0:
            logger.warning(
                "cli_logout_failed backend=%s returncode=%s", profile.backend, None if done is None else done.returncode
            )
    store.clear(backend)
    return await resolve_login_state(
        store, backend, executable=executable, env=cli_env, timeout_seconds=timeout_seconds
    )
