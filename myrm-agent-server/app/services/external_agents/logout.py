"""Logging an external agent's CLI out of the login in one home.

[INPUT]
- myrm_agent_harness.toolkits.acp.auth::CredentialState, CredentialStore, log_out, profile_for (POS: The ACP subscription auth subsystem)
- myrm_agent_harness.toolkits.acp.core.backend_detector::BackendDetector (POS: CLI backend detection)

[OUTPUT]
- log_out_of_home: Log a backend out of the home an environment reaches, and return the state its CLI reports.

[POS]
Business-layer logout shared by the logout endpoint and the removal of an account: both must reach a login the CLI keeps
outside the home (the macOS Keychain), which deleting files or a directory cannot.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.acp.auth import CredentialState


async def log_out_of_home(backend: str, env: Mapping[str, str]) -> CredentialState:
    """Log ``backend`` out of the home ``env`` reaches: its installed CLI first, then the credential files.

    The CLI is the one detected on this machine; without one only the files are removed. The returned state is what the
    CLI reports afterwards, so a login that could not be removed is visible to the caller.

    Raises:
        ValueError: ``backend`` is not a known agent.
    """
    from myrm_agent_harness.toolkits.acp.auth import CredentialStore, log_out, profile_for
    from myrm_agent_harness.toolkits.acp.core.backend_detector import BackendDetector

    profile = profile_for(backend)
    detected = await BackendDetector().detect(include_version=False)
    name = profile.backend if profile is not None else backend
    executable = next((found.path for found in detected if found.name == name), None)
    return await log_out(CredentialStore(env), backend, executable=executable, env=env)
