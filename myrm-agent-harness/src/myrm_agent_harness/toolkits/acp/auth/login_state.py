"""The login state a CLI reports about itself: the one source that knows where its credentials really live.

A credential-file check misses a login kept elsewhere (Claude Code on macOS keeps it in the Keychain, a key can sit in
the CLI's own settings) and trusts a file the CLI may no longer honour. A CLI that has a status command is asked; one
that has none, or does not answer, falls back to the credential file.

[INPUT]
- toolkits.acp.drivers::driver_for (POS: The contract of every known agent: its login, launch, transport and permission facts)
- toolkits.acp.auth.credential_store::AuthStatus, CredentialState, CredentialStore (POS: Credential persistence and detection layer for the ACP auth subsystem)
- toolkits.acp.core.probe::ProbeResult, run_probe (POS: Process primitive of the detection and login-state checks)

[OUTPUT]
- LoginReport: What a CLI said about its own login.
- probe_login: Ask a CLI for its login state; ``None`` when it cannot say.
- resolve_login_state: The authentication state of a backend, from the CLI's own report when it gives one.

[POS]
Login-state detection of the ACP auth subsystem; powers the "logged in" badge and the pre-flight auth check.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from myrm_agent_harness.toolkits.acp.auth.credential_store import AuthStatus, CredentialState, CredentialStore
from myrm_agent_harness.toolkits.acp.core.probe import ProbeResult, run_probe
from myrm_agent_harness.toolkits.acp.drivers import driver_for

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LoginReport:
    """What a CLI said about its own login: whether, and by which method (``""`` when it does not say)."""

    logged_in: bool
    method: str = ""


def _parse_claude(result: ProbeResult) -> LoginReport | None:
    """``claude auth status`` prints JSON with ``loggedIn`` and ``authMethod`` on stdout; it exits 1 when logged out."""
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("loggedIn"), bool):
        return None
    method = data.get("authMethod")
    return LoginReport(data["loggedIn"], method if isinstance(method, str) and method != "none" else "")


def _parse_codex(result: ProbeResult) -> LoginReport | None:
    """``codex login status`` says so on stderr and exits 0 when logged in (naming the method), 1 when not."""
    if result.returncode == 0:
        said = f"{result.stdout}\n{result.stderr}".lower()
        return LoginReport(True, "api_key" if "api key" in said else "subscription")
    if result.returncode == 1:
        return LoginReport(False)
    return None


_PARSERS: dict[str, Callable[[ProbeResult], LoginReport | None]] = {"claude": _parse_claude, "codex": _parse_codex}


async def probe_login(
    backend: str,
    *,
    executable: str,
    env: Mapping[str, str],
    timeout_seconds: float = 15.0,
) -> LoginReport | None:
    """Ask the CLI itself; ``None`` when it has no status command, did not answer, or answered in an unknown shape."""
    profile = driver_for(backend)
    parse = _PARSERS.get(profile.backend) if profile is not None else None
    if profile is None or parse is None or not profile.login_probe:
        return None
    result = await run_probe([executable, *profile.login_probe], env=env, timeout_seconds=timeout_seconds)
    reported = parse(result) if result is not None else None
    if reported is None:
        logger.debug("login_probe_unanswered backend=%s", profile.backend)
    return reported


async def resolve_login_state(
    store: CredentialStore,
    backend: str,
    *,
    executable: str | None,
    env: Mapping[str, str],
    timeout_seconds: float = 15.0,
) -> CredentialState:
    """The authentication state of ``backend``: the CLI's own report when it gives one, else the credential file.

    ``env`` is what the CLI is run with, so the answer is the state a delegated turn would see.
    """
    file_state = store.state(backend)
    if executable is None:
        return file_state
    reported = await probe_login(backend, executable=executable, env=env, timeout_seconds=timeout_seconds)
    if reported is None:
        return file_state
    if reported.logged_in:
        return CredentialState(
            backend=file_state.backend,
            status=AuthStatus.AUTHENTICATED,
            credential_path=file_state.credential_path,
            detail=reported.method,
        )
    return CredentialState(
        backend=file_state.backend, status=AuthStatus.NOT_AUTHENTICATED, detail="the CLI reports no login"
    )
