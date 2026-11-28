"""The shape of a known agent's contract: one record that every layer reads instead of keeping its own copy.

[INPUT]
- toolkits.acp.types::PermissionMode (POS: Core type definitions for the ACP runtime system)

[OUTPUT]
- LoginStrategy: Who runs a CLI's interactive login: the host, which relays what the CLI prints, or the user in a terminal.
- Transport: How a known agent is driven: as a CLI process, over ACP, or over its own app server.
- PermissionFlags: The flags of a command line that choose what an agent may do without asking, and how they are taken out of a config's args.
- DriverSpec: Per-agent contract: what it is called, how it is installed, driven, logged in to and made to ask.

[POS]
What a contract is made of, not which agents have one (``registry``) nor the command lines derived from it
(``launch``). A spec that contradicts itself (an ACP agent with no permission flags, a CLI agent with a permission mode
it cannot be put in, a blank title) cannot be built, so a half-added agent fails when the harness is imported rather
than when someone first starts it.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Literal, get_args

from myrm_agent_harness.toolkits.acp.types import PermissionMode

# How an agent is driven: as a CLI process whose output is parsed, over the Agent Client Protocol, or over the JSON-RPC
# app server of Codex.
Transport = Literal["cli", "acp", "app_server"]

# A backend is the bare lowercase name of its executable: what a command is found by once its path and extension are gone.
_BACKEND_NAME = re.compile(r"[a-z0-9][a-z0-9_-]*")


class LoginStrategy(StrEnum):
    """Who runs the interactive subscription login of a CLI backend."""

    # The host runs the CLI's login and relays the link or the device code it prints.
    BROWSER_OAUTH = "browser_oauth"
    # The user runs the CLI's own login in a terminal; the host shows the command and then asks the CLI who is logged in.
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class PermissionFlags:
    """The flags that choose what an agent may do without asking, spelled as its command line spells them.

    The host sets the mode itself, so a configured flag of this set is taken away: it would contradict the mode the
    user selected or walk past the host's policy. The set belongs to one agent because a flag name can mean something
    else in another CLI: a global strip would silently disable what the user asked for (gemini's own ``--sandbox``).
    """

    names: frozenset[str]  # every flag, short and long spellings alike
    value_names: frozenset[str]  # the flags that take a value, as the next arg or as ``--flag=value``

    def __post_init__(self) -> None:
        if not self.value_names <= self.names:
            msg = f"Flags that take a value must be flags of the set: {sorted(self.value_names - self.names)}"
            raise ValueError(msg)

    def strip(self, args: Sequence[str]) -> tuple[list[str], list[str]]:
        """``args`` without these flags (and the value of one that takes it), and what was removed."""
        inline_prefixes = tuple(f"{name}=" for name in self.value_names)
        kept: list[str] = []
        removed: list[str] = []
        skip_value = False
        for arg in args:
            if skip_value:
                skip_value = False
                removed.append(arg)
                continue
            if arg in self.names or arg.startswith(inline_prefixes):
                skip_value = arg in self.value_names
                removed.append(arg)
                continue
            kept.append(arg)
        return kept, removed


@dataclass(frozen=True, slots=True)
class DriverSpec:
    """Everything the host knows about one agent: its names, its install, its login, its launch and its permissions.

    Paths are resolved against a supplied environment so the control plane can relocate a CLI's home (e.g.
    ``CODEX_HOME`` → ``/persistent/.codex``) without any change here.
    """

    # The bare name of the executable; the key every other layer finds the agent by.
    backend: str
    # What a person calls it ("Claude Code"): the label of the button that adds it.
    title: str
    # The name an agent added from this entry starts with, and what the model is told it is.
    default_name: str
    description: str
    # The npm package the toolchain installs it from.
    npm_package: str
    home_env: str  # env var that relocates the CLI home dir
    home_dir: str  # default home subdir under $HOME (e.g. ".codex")
    credential_files: tuple[str, ...]  # candidate credential filenames within the home dir
    login_strategy: LoginStrategy
    # Args appended to the command to start login; empty: the CLI asks to sign in when it starts.
    login_args: tuple[str, ...]
    logout_args: tuple[str, ...] | None  # args that make the CLI remove its own login, wherever it keeps it
    api_key_env: tuple[str, ...]  # provider key env names used in api_key mode
    # The transport the agent's own contract calls for, and the args that start it that way (never permission flags,
    # except the one an ACP agent needs to ask at all): a CLI agent's baseline headless invocation; the flag that puts
    # an ACP agent in ACP mode, then the one that makes it ask the host before it acts (its own settings may default
    # to a mode that never asks); the subcommand that starts an app server.
    transport: Transport = "cli"
    launch_args: tuple[str, ...] = ()
    # The session mode an ACP agent reports once it asks the host; anything else in ``safe`` mode is not left standing.
    acp_ask_mode: str | None = None
    # The args that make the CLI report its own login state; empty: it has no such command.
    login_probe: tuple[str, ...] = ()
    # The login for a browser on another machine than the CLI: a loopback callback cannot reach it from there, so the
    # CLI is asked for the flow that works from anywhere. Empty: ``login_args`` already works from anywhere.
    remote_login_args: tuple[str, ...] = ()
    # What a home of its own needs beyond ``home_env``. ``home_env_is_base``: the variable stands in for ``$HOME``, so
    # the CLI's state lives in ``<value>/<home_dir>`` rather than in the value itself. ``isolation_env``: variables that
    # keep the login inside the home; a CLI that keeps it in the operating system's keychain unless told otherwise
    # shares one login between every home.
    home_env_is_base: bool = False
    isolation_env: tuple[tuple[str, str], ...] = ()
    # The flags the host owns, taken out of a config's args: every CLI and ACP agent has them, an app-server agent is
    # given its approval policy with every turn so no flag of a config can reach it.
    permission_flags: PermissionFlags | None = None
    # For a CLI agent, which args put it in each permission mode; it has nobody to ask, so the mode is a flag.
    permission_mode_args: tuple[tuple[PermissionMode, tuple[str, ...]], ...] = ()
    # The agent connects to no MCP server (the shared memory is one) in a folder it has not been told to trust.
    mcp_needs_trusted_folder: bool = False

    def __post_init__(self) -> None:
        problems = self._contradictions()
        if problems:
            msg = f"The contract of {self.backend!r} contradicts itself: {'; '.join(problems)}"
            raise ValueError(msg)

    def _contradictions(self) -> list[str]:
        problems: list[str] = []
        if not _BACKEND_NAME.fullmatch(self.backend):
            problems.append("the backend must be the bare lowercase name of its executable")
        texts = (
            ("title", self.title),
            ("default_name", self.default_name),
            ("description", self.description),
            ("npm_package", self.npm_package),
        )
        if blank := [name for name, text in texts if not text.strip()]:
            problems.append(f"{', '.join(blank)} must not be blank")

        if self.transport == "app_server":
            if self.permission_flags is not None:
                problems.append(
                    "an app-server agent takes its approval policy with every turn, so it has no permission flags"
                )
        elif self.permission_flags is None:
            problems.append(
                "a CLI or ACP agent needs permission_flags, or a configured flag could contradict the host's mode"
            )

        if self.transport == "cli":
            problems.extend(self._mode_args_problems())
        elif self.permission_mode_args:
            problems.append("only a CLI agent has permission_mode_args: any other asks the host or is given its policy")

        if self.transport == "acp":
            if not (self.acp_ask_mode or "").strip():
                problems.append("an ACP agent needs the session mode it reports once it asks the host (acp_ask_mode)")
        elif self.acp_ask_mode is not None:
            problems.append("only an ACP agent has an acp_ask_mode")
        if self.transport != "cli" and not self.launch_args:
            problems.append("an ACP or app-server agent needs the launch_args that start it that way")
        return problems

    def _mode_args_problems(self) -> list[str]:
        """What is wrong with the way a CLI agent is put in each permission mode."""
        modes = [mode for mode, _ in self.permission_mode_args]
        if sorted(modes) != sorted(get_args(PermissionMode)):
            return [f"a CLI agent maps every permission mode exactly once, not {modes}"]
        flags = self.permission_flags.names if self.permission_flags is not None else frozenset()
        return [
            f"the args of mode {mode!r} must start with a flag of permission_flags, or a configured one would repeat it"
            for mode, args in self.permission_mode_args
            if not args or args[0] not in flags
        ]

    def resolve_home(self, env: Mapping[str, str]) -> Path:
        """Resolve the CLI home directory, honouring the relocation variable if set."""
        override = env.get(self.home_env)
        if override:
            base = Path(override).expanduser()
            return base / self.home_dir if self.home_env_is_base else base
        home = env.get("HOME") or os.path.expanduser("~")
        return Path(home) / self.home_dir

    @property
    def relocation_vars(self) -> tuple[str, ...]:
        """The variables that relocate this CLI's home and keep its login inside it."""
        return (self.home_env, *(name for name, _ in self.isolation_env))

    def isolated_env(self, root: Path) -> dict[str, str]:
        """The environment that makes this CLI keep all its state and its login under ``root``."""
        return {self.home_env: str(root), **dict(self.isolation_env)}

    def resolve_credential_paths(self, env: Mapping[str, str]) -> list[Path]:
        """Candidate credential file paths in priority order."""
        base = self.resolve_home(env)
        return [base / name for name in self.credential_files]

    def login_command(self, *, remote: bool) -> tuple[str, ...]:
        """The args that start the login, for a browser on another machine than the CLI when ``remote``."""
        return self.remote_login_args if remote and self.remote_login_args else self.login_args

    @property
    def permission_modes(self) -> tuple[str, ...]:
        """The permission modes a CLI agent can be put in; none for an agent that is not driven as a CLI process."""
        return tuple(mode for mode, _ in self.permission_mode_args)

    def permission_args(self, mode: str) -> tuple[str, ...] | None:
        """The args that put a CLI agent in ``mode``; ``None`` for a mode it has no mapping for."""
        return next((args for name, args in self.permission_mode_args if name == mode), None)

    def strip_permission_flags(self, args: Sequence[str]) -> tuple[list[str], list[str]]:
        """``args`` without the permission flags the host owns for this agent, and what was removed."""
        if self.permission_flags is None:
            return list(args), []
        return self.permission_flags.strip(args)
