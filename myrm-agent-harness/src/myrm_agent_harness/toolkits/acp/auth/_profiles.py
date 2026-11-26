"""Authentication profiles for known external CLI agent backends.

Captures, per backend, where its subscription login state lives, how to launch an
interactive login, and which provider keys map to api-key mode. Business and
control-plane layers read these to drive GUI login, status badges, and credential
persistence without hard-coding CLI specifics anywhere upstream.

[INPUT]
no — leaf module, standard library only

[OUTPUT]
- LoginStrategy: How a CLI performs interactive login.
- Transport: How a known agent is driven: as a CLI process or over ACP.
- AuthProfile: Per-backend contract: login state, launch, transport.
- profile_for: Resolve an AuthProfile from a backend name or command (a name that merely contains a backend's counts).
- command_profile: Resolve the AuthProfile of a command that is exactly a known agent's executable.
- effective_transport: The transport an agent really runs over: a known agent's contract wins over a stored choice.
- all_profiles, known_backends: Every registered profile, and their names.
- cli_launch_args: Baseline non-interactive launch args for a CLI backend.

[POS]
The contract of every known agent: where its login lives, how to log in, how it is launched and over which transport.
Business and control-plane layers read it instead of hard-coding agent specifics; the launch and permission arguments
derived from it are resolved in ``_launch_args``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal

# How an agent is driven: as a CLI process whose output is parsed, or over the Agent Client Protocol.
Transport = Literal["cli", "acp"]


class LoginStrategy(StrEnum):
    """How a CLI backend performs an interactive subscription login."""

    DEVICE_CODE = "device_code"  # CLI prints a URL/code; user authorizes in a browser, CLI polls
    BROWSER_OAUTH = "browser_oauth"  # CLI opens a browser and runs a local loopback callback
    SETUP_TOKEN = "setup_token"  # CLI prints a URL; user pastes the returned auth code back via stdin
    MANUAL_IMPORT = "manual_import"  # no scriptable login on this host; only credential import works


@dataclass(frozen=True, slots=True)
class AuthProfile:
    """Authentication characteristics of a known CLI agent backend.

    Paths are resolved against a supplied environment so the control plane can
    relocate a CLI's home (e.g. ``CODEX_HOME`` → ``/persistent/.codex``) without
    any change here.
    """

    backend: str
    home_env: str | None  # env var that overrides the CLI home dir, if the CLI honours one
    home_dir: str  # default home subdir under $HOME (e.g. ".codex")
    credential_files: tuple[str, ...]  # candidate credential filenames within the home dir
    login_strategy: LoginStrategy
    login_args: tuple[str, ...]  # args appended to the command to start login ("" → not scriptable)
    logout_args: tuple[str, ...] | None
    needs_code_input: bool  # whether login expects the user to paste a code back via stdin
    api_key_env: tuple[str, ...]  # provider key env names used in api_key mode
    # The transport the agent's own contract calls for. A CLI agent is run headless with ``cli_launch_args``; an ACP
    # agent is started with ``acp_launch_args`` and put to the host's permission policy.
    transport: Transport = "cli"
    cli_launch_args: tuple[str, ...] = ()  # baseline non-interactive invocation args (never permission flags)
    # Entry args of an ACP agent: the flag that puts it in ACP mode, then the one that makes it ask the host before it
    # acts (its own settings may default to a mode that never asks).
    acp_launch_args: tuple[str, ...] = ()
    # The session mode an ACP agent reports once it asks the host; anything else in ``safe`` mode is not left standing.
    acp_ask_mode: str | None = None
    login_probe: tuple[
        str, ...
    ] = ()  # args that make the CLI report its own login state; empty: it has no such command

    def resolve_home(self, env: Mapping[str, str]) -> Path:
        """Resolve the CLI home directory, honouring an override env var if set."""
        if self.home_env:
            override = env.get(self.home_env)
            if override:
                return Path(override).expanduser()
        home = env.get("HOME") or os.path.expanduser("~")
        return Path(home) / self.home_dir

    def resolve_credential_paths(self, env: Mapping[str, str]) -> list[Path]:
        """Candidate credential file paths in priority order."""
        base = self.resolve_home(env)
        return [base / name for name in self.credential_files]

    @property
    def scriptable_login(self) -> bool:
        """Whether an interactive login can be driven by spawning the CLI."""
        return bool(self.login_args) and self.login_strategy is not LoginStrategy.MANUAL_IMPORT


# Per-backend profiles, in the order agents are detected and listed. Credential locations and home-override vars
# below reflect each CLI's documented behaviour; the manual-import fallback (credential_store) guarantees a
# working path even where scripted login is unavailable.
_PROFILES: dict[str, AuthProfile] = {
    "claude": AuthProfile(
        backend="claude",
        home_env="CLAUDE_CONFIG_DIR",
        home_dir=".claude",
        credential_files=(".credentials.json",),
        login_strategy=LoginStrategy.SETUP_TOKEN,
        login_args=("setup-token",),
        logout_args=None,
        needs_code_input=True,
        api_key_env=("ANTHROPIC_API_KEY",),
        login_probe=("auth", "status"),
        cli_launch_args=("-p", "--output-format", "stream-json", "--verbose"),
    ),
    "codex": AuthProfile(
        backend="codex",
        home_env="CODEX_HOME",
        home_dir=".codex",
        credential_files=("auth.json",),
        login_strategy=LoginStrategy.DEVICE_CODE,
        login_args=("login",),
        logout_args=("logout",),
        needs_code_input=False,
        api_key_env=("OPENAI_API_KEY",),
        login_probe=("login", "status"),
        # A delegated workspace is not necessarily a git repository, and `exec` refuses to start outside one
        # unless told otherwise. The sandbox the permission mode selects is unaffected by the flag.
        cli_launch_args=("exec", "--json", "--skip-git-repo-check"),
    ),
    "gemini": AuthProfile(
        backend="gemini",
        home_env=None,
        home_dir=".gemini",
        credential_files=("oauth_creds.json",),
        login_strategy=LoginStrategy.BROWSER_OAUTH,
        login_args=(),
        logout_args=None,
        needs_code_input=False,
        api_key_env=("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        transport="acp",
        acp_launch_args=("--acp", "--approval-mode", "default"),
        acp_ask_mode="default",
    ),
    "qwen": AuthProfile(
        backend="qwen",
        home_env=None,
        home_dir=".qwen",
        credential_files=("oauth_creds.json",),
        login_strategy=LoginStrategy.BROWSER_OAUTH,
        login_args=(),
        logout_args=None,
        needs_code_input=False,
        api_key_env=("DASHSCOPE_API_KEY", "QWEN_API_KEY"),
        transport="acp",
        acp_launch_args=("--acp", "--approval-mode", "default"),
        acp_ask_mode="default",
    ),
}


def _normalize(token: str) -> str:
    """Reduce a command path or agent name to a bare lowercase backend key."""
    base = os.path.basename(token.strip()).lower()
    return base.split(".", 1)[0]  # drop extensions like .cmd / .exe


def profile_for(backend_or_command: str) -> AuthProfile | None:
    """Resolve an AuthProfile from a backend name or executable command.

    Matches the executable basename first (``/usr/bin/codex`` → ``codex``), then
    falls back to a substring scan so configured names like ``codex-cli`` or
    ``claude-code`` still resolve.
    """
    if not backend_or_command:
        return None
    key = _normalize(backend_or_command)
    direct = _PROFILES.get(key)
    if direct is not None:
        return direct
    for name, profile in _PROFILES.items():
        if name in key:
            return profile
    return None


def command_profile(command: str | None) -> AuthProfile | None:
    """The profile of a command that is exactly a known agent's executable (``/usr/bin/gemini``, ``gemini.cmd``).

    ``profile_for`` also accepts a name that merely contains a backend's (``claude-code``), which suits resolving the
    login of an agent by its configured name. How an agent is started and driven is decided only for the real
    executable: an adapter or wrapper that happens to carry an agent's name is a command of its own.
    """
    if not command:
        return None
    return _PROFILES.get(_normalize(command))


def effective_transport[Configured: str](command: str | None, configured: Configured) -> Configured | Transport:
    """The transport ``command`` really runs over: a known agent's contract wins over the choice stored for it.

    Any other command keeps ``configured`` as it is, a value this module does not recognise included, so the layer that
    stored it can still report it.
    """
    profile = command_profile(command)
    return profile.transport if profile is not None else configured


def all_profiles() -> tuple[AuthProfile, ...]:
    """Every registered profile."""
    return tuple(_PROFILES.values())


def known_backends() -> tuple[str, ...]:
    """Names of all backends with a registered auth profile."""
    return tuple(_PROFILES)


def cli_launch_args(backend_or_command: str) -> list[str]:
    """Baseline non-interactive launch args for a CLI backend, or an empty list.

    Business layers call this instead of hard-coding per-CLI arguments, so all CLI
    invocation knowledge stays in this registry and cannot drift across layers.
    """
    profile = profile_for(backend_or_command)
    return list(profile.cli_launch_args) if profile else []
