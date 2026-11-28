"""What an external agent's CLI says about the login in one home, and which CLI that is.

[INPUT]
- myrm_agent_harness.toolkits.acp.auth::CredentialStore, cli_base_env, profile_for, resolve_login_state (POS: The ACP subscription auth subsystem)
- myrm_agent_harness.toolkits.acp.core.backend_detector::BackendDetector (POS: CLI backend detection)

[OUTPUT]
- login_state: The login in the CLI's own home or in a named account's home.
- detected_executable: The CLI of an agent as detected on this machine.

[POS]
Business-layer read of a login, shared by the status listing, the account listing, the terminal-login answer and the
logout, so that all of them report and reach what a delegated turn would.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.acp.auth import Account, CredentialState


async def login_state(backend: str, executable: str | None, account: Account | None = None) -> CredentialState:
    """The login in the home of ``account`` (the CLI's own home without one).

    What the CLI says about it where it can say it (Claude Code keeps its login in the macOS Keychain, which no
    credential-file check sees); a CLI without a status command, or none installed, is judged by its credential file.
    """
    from myrm_agent_harness.toolkits.acp.auth import CredentialStore, cli_base_env, resolve_login_state

    env = cli_base_env(executable, os.environ)
    if account is not None:
        env = account.env(env)
    return await resolve_login_state(CredentialStore(env), backend, executable=executable, env=env)


async def detected_executable(backend: str) -> str | None:
    """The path of the CLI of ``backend`` detected on this machine, or ``None`` when there is none.

    Raises:
        ValueError: ``backend`` is not a known agent.
    """
    from myrm_agent_harness.toolkits.acp.auth import profile_for
    from myrm_agent_harness.toolkits.acp.core.backend_detector import BackendDetector

    profile = profile_for(backend)
    if profile is None:
        msg = f"Unknown external agent backend: {backend}"
        raise ValueError(msg)
    detected = await BackendDetector().detect(include_version=False)
    return next((found.path for found in detected if found.name == profile.backend), None)
