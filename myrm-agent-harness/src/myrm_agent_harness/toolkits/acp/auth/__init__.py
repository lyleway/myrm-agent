"""Subscription authentication subsystem for external CLI agent backends.

Lets a delegated CLI (Codex / Claude Code / Gemini / Qwen …) run on the user's own
model subscription instead of a metered API key. Provides these framework-level
mechanisms, all GUI/SaaS-driven by the business layer:

- login detection — is this backend logged in? The CLI's own report where it has one, else its credential file (pre-flight checks, status badges)
- login — either the host drives ``<cli> login`` and streams its URL/code to the user, or the user runs the CLI's own login in a terminal and the host describes the command; logout the way the CLI does it
- named accounts — more than one login of the same CLI, each kept in a home of its own

The host never holds a credential: the CLI makes its own login and keeps it, and nothing here accepts one to store.

[INPUT]
- toolkits.acp.auth.accounts::Account, AccountError, AccountExistsError, AccountNotFoundError, AccountStore, is_valid_account_name (POS: Account registry of the ACP auth subsystem.)
- toolkits.acp.auth.credential_store::AuthStatus, CredentialState, CredentialStore (POS: Credential detection layer.)
- toolkits.acp.auth.login_session::AuthEvent, AuthEventType, CliLoginSession (POS: CLI login session management.)
- toolkits.acp.auth.terminal_login::Shell, TerminalLogin, default_shell, terminal_login (POS: The login a user runs in a terminal.)
- toolkits.acp.auth.launch_env::account_env, cli_base_env (POS: The environment every launch of a CLI agent starts from.)
- toolkits.acp.auth.login_state::LoginReport, probe_login, resolve_login_state (POS: Login state a CLI reports about itself.)
- toolkits.acp.auth.logout::log_out (POS: Logout of the ACP auth subsystem.)

[OUTPUT]
- Account, AccountError, AccountExistsError, AccountNotFoundError, AccountStore, is_valid_account_name
- AuthStatus, CredentialState, CredentialStore
- AuthEventType, AuthEvent, CliLoginSession
- Shell, TerminalLogin, default_shell, terminal_login
- account_env, cli_base_env
- LoginReport, probe_login, resolve_login_state
- log_out

[POS]
Public surface of the ACP auth subsystem.
"""

from __future__ import annotations

from myrm_agent_harness.toolkits.acp.auth.accounts import (
    Account,
    AccountError,
    AccountExistsError,
    AccountNotFoundError,
    AccountStore,
    is_valid_account_name,
)
from myrm_agent_harness.toolkits.acp.auth.credential_store import (
    AuthStatus,
    CredentialState,
    CredentialStore,
)
from myrm_agent_harness.toolkits.acp.auth.launch_env import account_env, cli_base_env
from myrm_agent_harness.toolkits.acp.auth.login_session import (
    AuthEvent,
    AuthEventType,
    CliLoginSession,
)
from myrm_agent_harness.toolkits.acp.auth.login_state import LoginReport, probe_login, resolve_login_state
from myrm_agent_harness.toolkits.acp.auth.logout import log_out
from myrm_agent_harness.toolkits.acp.auth.terminal_login import Shell, TerminalLogin, default_shell, terminal_login

__all__ = [
    "Account",
    "AccountError",
    "AccountExistsError",
    "AccountNotFoundError",
    "AccountStore",
    "AuthEvent",
    "AuthEventType",
    "AuthStatus",
    "CliLoginSession",
    "CredentialState",
    "CredentialStore",
    "LoginReport",
    "Shell",
    "TerminalLogin",
    "account_env",
    "cli_base_env",
    "default_shell",
    "is_valid_account_name",
    "log_out",
    "probe_login",
    "resolve_login_state",
    "terminal_login",
]
