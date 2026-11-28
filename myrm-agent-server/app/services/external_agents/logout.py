"""Logging an external agent's CLI out of the login in one home.

[INPUT]
- myrm_agent_harness.toolkits.acp.auth::CredentialState, CredentialStore, log_out (POS: The ACP subscription auth subsystem)
- app.services.external_agents.login_state::detected_executable (POS: The CLI of an agent as detected on this machine)

[OUTPUT]
- log_out_of_home: Log a backend out of the home an environment reaches, and return the state its CLI reports.

[POS]
Business-layer logout shared by the logout endpoint and the removal of an account: both must reach a login the CLI keeps
outside the home (the macOS Keychain), which deleting files or a directory cannot.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from app.services.external_agents.login_state import detected_executable

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.acp.auth import CredentialState


async def log_out_of_home(backend: str, env: Mapping[str, str]) -> CredentialState:
    """Log ``backend`` out of the home ``env`` reaches: its installed CLI first, then the credential files.

    The CLI is the one detected on this machine; without one only the files are removed. The returned state is what the
    CLI reports afterwards, so a login that could not be removed is visible to the caller.

    Raises:
        ValueError: ``backend`` is not a known agent.
    """
    from myrm_agent_harness.toolkits.acp.auth import CredentialStore, log_out

    executable = await detected_executable(backend)
    return await log_out(CredentialStore(env), backend, executable=executable, env=env)
