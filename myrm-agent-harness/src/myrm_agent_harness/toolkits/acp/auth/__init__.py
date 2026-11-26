"""Subscription authentication subsystem for external CLI agent backends.

Lets a delegated CLI (Codex / Claude Code / Gemini / Qwen …) run on the user's own
model subscription instead of a metered API key. Provides three framework-level
mechanisms, all GUI/SaaS-driven by the business layer:

- login detection — is this backend logged in? The CLI's own report where it has one, else its credential file (pre-flight checks, status badges)
- interactive login — drive ``<cli> login`` and stream URLs/codes to the user
- credential import — the universal fallback: persist a credential captured elsewhere

[INPUT]
- toolkits.acp.auth._profiles::AuthProfile, LoginStrategy, Transport, cli_launch_args, effective_transport, known_backends, profile_for (POS: Authentication profile registry.)
- toolkits.acp.auth.credential_store::AuthStatus, CredentialState, CredentialStorageDisabledError, CredentialStore (POS: Credential persistence and detection.)
- toolkits.acp.auth.login_session::AuthEvent, AuthEventType, CliLoginSession (POS: CLI login session management.)
- toolkits.acp.auth.launch_env::cli_base_env (POS: The environment every launch of a CLI agent starts from.)
- toolkits.acp.auth.login_state::LoginReport, probe_login, resolve_login_state (POS: Login state a CLI reports about itself.)

[OUTPUT]
- LoginStrategy, AuthProfile, Transport, profile_for, known_backends, cli_launch_args, effective_transport
- AuthStatus, CredentialState, CredentialStorageDisabledError, CredentialStore
- AuthEventType, AuthEvent, CliLoginSession
- cli_base_env
- LoginReport, probe_login, resolve_login_state

[POS]
Public surface of the ACP auth subsystem.
"""

from __future__ import annotations

from myrm_agent_harness.toolkits.acp.auth._profiles import (
    AuthProfile,
    LoginStrategy,
    Transport,
    cli_launch_args,
    effective_transport,
    known_backends,
    profile_for,
)
from myrm_agent_harness.toolkits.acp.auth.credential_store import (
    AuthStatus,
    CredentialState,
    CredentialStorageDisabledError,
    CredentialStore,
)
from myrm_agent_harness.toolkits.acp.auth.launch_env import cli_base_env
from myrm_agent_harness.toolkits.acp.auth.login_session import (
    AuthEvent,
    AuthEventType,
    CliLoginSession,
)
from myrm_agent_harness.toolkits.acp.auth.login_state import LoginReport, probe_login, resolve_login_state

__all__ = [
    "AuthEvent",
    "AuthEventType",
    "AuthProfile",
    "AuthStatus",
    "CliLoginSession",
    "CredentialState",
    "CredentialStorageDisabledError",
    "CredentialStore",
    "LoginReport",
    "LoginStrategy",
    "Transport",
    "cli_base_env",
    "cli_launch_args",
    "effective_transport",
    "known_backends",
    "probe_login",
    "profile_for",
    "resolve_login_state",
]
