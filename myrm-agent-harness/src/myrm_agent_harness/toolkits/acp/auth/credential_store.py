"""Detect and clear subscription login state for CLI agent backends.

Pure, environment-aware filesystem operations against the per-backend credential
files declared in :mod:`._profiles`. Detection powers pre-flight auth checks and
status badges. The store never writes a credential: a login is made by the CLI
itself, so a credential the host never holds cannot be mishandled by it.

[INPUT]
- toolkits.acp.auth._profiles::profile_for (POS: Authentication profile registry.)

[OUTPUT]
- AuthStatus: Authentication state of a backend.
- CredentialState: Resolved auth state with the satisfying credential path.
- CredentialStore: Detect / clear subscription credentials.

[POS]
Credential detection layer for the ACP auth subsystem.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from myrm_agent_harness.toolkits.acp.auth._profiles import profile_for

logger = logging.getLogger(__name__)


class AuthStatus(StrEnum):
    """Authentication state of a backend."""

    AUTHENTICATED = "authenticated"  # a non-empty credential file is present
    NOT_AUTHENTICATED = "not_authenticated"  # profile known, but no credential found
    UNKNOWN = "unknown"  # no auth profile registered for this backend


@dataclass(frozen=True, slots=True)
class CredentialState:
    """Resolved authentication state for a single backend."""

    backend: str
    status: AuthStatus
    credential_path: str | None = None  # the file that satisfied the check, if any
    detail: str = ""

    @property
    def authenticated(self) -> bool:
        return self.status is AuthStatus.AUTHENTICATED


class CredentialStore:
    """Detect and clear CLI subscription credentials.

    Constructed with an environment snapshot so the control plane can relocate a
    CLI's home (e.g. ``CODEX_HOME`` → a persistent volume) and have detection and
    clearing both target the same location transparently.
    """

    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env: dict[str, str] = dict(env if env is not None else os.environ)

    def state(self, backend: str) -> CredentialState:
        """Resolve the current authentication state of a backend."""
        profile = profile_for(backend)
        if profile is None:
            return CredentialState(backend=backend, status=AuthStatus.UNKNOWN, detail="no auth profile")

        for path in profile.resolve_credential_paths(self._env):
            if _has_credential(path):
                return CredentialState(
                    backend=profile.backend,
                    status=AuthStatus.AUTHENTICATED,
                    credential_path=str(path),
                )
        return CredentialState(
            backend=profile.backend,
            status=AuthStatus.NOT_AUTHENTICATED,
            detail="no credential file found",
        )

    def is_authenticated(self, backend: str) -> bool:
        """Whether a non-empty subscription credential is present for the backend."""
        return self.state(backend).authenticated

    def clear(self, backend: str) -> CredentialState:
        """Remove all known credential files for a backend (logout)."""
        profile = profile_for(backend)
        if profile is None:
            msg = f"No auth profile for backend {backend!r}"
            raise ValueError(msg)
        removed = 0
        for path in profile.resolve_credential_paths(self._env):
            try:
                path.unlink()
                removed += 1
            except FileNotFoundError:
                continue
            except OSError:
                logger.warning("credential_clear_failed backend=%s path=%s", profile.backend, path, exc_info=True)
        logger.info("credential_cleared backend=%s removed=%d", profile.backend, removed)
        return CredentialState(backend=profile.backend, status=AuthStatus.NOT_AUTHENTICATED)


def _has_credential(path: Path) -> bool:
    """Whether a path is an existing, non-empty regular file."""
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False
