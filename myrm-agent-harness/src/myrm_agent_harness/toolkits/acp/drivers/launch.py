"""The command line a known agent is started with: one resolver per transport.

[INPUT]
- toolkits.acp.drivers.registry::command_driver, driver_for (POS: The known agents and the lookups that find one by backend name or command)
- toolkits.acp.types::AuthMode (POS: Core type definitions for the ACP runtime system)

[OUTPUT]
- resolve_cli_args: Merge a CLI agent's baseline launch args with the config args and the permission-mode args.
- AcpLaunch, resolve_acp_args: The launch args of an agent driven over ACP: its contract's entry args plus what the config adds.
- AppServerLaunch, resolve_app_server_args: The launch args of an agent driven over its app server: the subcommand, the configuration overrides the config carries and the credential store its auth mode needs.

[POS]
The only place the permission facts of a known agent's contract reach a command line. A CLI agent has nobody to ask, so
its permission mode is a flag this module sets; an ACP agent asks the host, so its mode is the one contract flag that
makes it ask, and every flag a config carries that would make it skip asking is dropped; an app-server agent is given
its approval policy and sandbox with every turn, so no flag of a config can reach it but the configuration overrides it
understands. A command that is not a known agent keeps its args as configured: no contract exists for it and its flags
may mean something unrelated.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from myrm_agent_harness.toolkits.acp.drivers.registry import command_driver, driver_for
from myrm_agent_harness.toolkits.acp.types import AuthMode


def resolve_cli_args(
    backend_or_command: str,
    configured_args: list[str],
    permission_mode: str,
) -> list[str]:
    """Merge baseline launch args with config args and the permission-mode args.

    Deterministic order: permission args, then baseline args, then configured args
    minus anything the permission mapping owns or the baseline already says. The mapping
    is the sole owner of the permission flags, so a config carrying an explicit (often
    stale) flag can never contradict the mode the user selected. A config that restates a
    baseline option (a preset's ``--output-format stream-json``) adds nothing; the config's own repeated
    options all stay, since ``-c a=1 -c b=2`` is two settings and not a duplicate.
    """
    spec = driver_for(backend_or_command)
    if spec is None or spec.transport != "cli":
        # Not a CLI agent we know: we own no permission contract for it, so args pass through
        # untouched rather than stripping flags that may mean something else entirely.
        return list(configured_args)

    mode_args = spec.permission_args(permission_mode)
    if mode_args is None:
        # A mode the contract does not name would start the CLI under its own permission defaults, which is never what
        # the caller asked for; refusing to launch is the only fail-closed answer.
        msg = f"Unknown permission mode {permission_mode!r}; expected one of {sorted(spec.permission_modes)}"
        raise ValueError(msg)
    merged: list[str] = list(mode_args)
    merged.extend(spec.launch_args)
    owned, _ = spec.strip_permission_flags(configured_args)
    merged.extend(_without_restated(owned, spec.launch_args))
    return merged


@dataclass(frozen=True, slots=True)
class AcpLaunch:
    """The args an ACP agent is started with, and the configured args dropped because they would stop it asking."""

    args: list[str]
    dropped: list[str]


def resolve_acp_args(command: str, configured_args: list[str]) -> AcpLaunch:
    """The launch args of an agent driven over ACP.

    A known ACP agent starts with its contract's entry args (the flag that puts it in ACP mode and the one that makes
    it ask the host) followed by what the config adds. The host's policy answers every request such an agent makes,
    so a configured flag that makes it act without asking (``--yolo``, ``--approval-mode``) is dropped: it would walk
    past the policy, whatever mode the host selected. A command that is not a known ACP agent (the exact executable
    name decides, so an adapter that merely contains an agent's name is not one) keeps its args as configured.
    """
    spec = command_driver(command)
    if spec is None or spec.transport != "acp":
        return AcpLaunch(list(configured_args), [])
    owned, dropped = spec.strip_permission_flags(configured_args)
    args = [*spec.launch_args, *_without_restated(owned, spec.launch_args)]
    return AcpLaunch(args, dropped)


@dataclass(frozen=True, slots=True)
class AppServerLaunch:
    """The args an app-server agent is started with, and the configured args dropped because it does not take them."""

    args: list[str]
    dropped: list[str]


# What a configured arg may carry to Codex's app server: configuration overrides and feature switches. Nothing else it
# takes (where to listen, which auth to sign out) is the host's to leave to a config.
_APP_SERVER_VALUE_FLAGS = frozenset({"-c", "--config", "--enable", "--disable"})
_APP_SERVER_SWITCHES = frozenset({"--strict-config"})
# The flags a config written for ``codex exec`` names a setting with; the app server takes the setting as an override.
_APP_SERVER_SETTING_FLAGS = {"-m": "model", "--model": "model", "-p": "profile", "--profile": "profile"}
# Where the app server keeps a login handed to it. An API key is the host's to hand over for one run only, so the
# store lives in memory: a file would replace the login the user made with the Codex CLI itself.
_APP_SERVER_MEMORY_CREDENTIALS = ("-c", 'cli_auth_credentials_store="ephemeral"')


def _flag_and_value(args: list[str], index: int) -> tuple[str, str | None, int]:
    """The flag at ``args[index]``, its value (``--flag value`` or ``--flag=value``) and how many args they take."""
    flag, has_inline, inline = args[index].partition("=")
    if has_inline:
        return flag, inline, 1
    value = args[index + 1] if index + 1 < len(args) else None
    return flag, value, 2


def resolve_app_server_args(command: str, configured_args: list[str], auth_mode: AuthMode) -> AppServerLaunch:
    """The launch args of an agent driven over its app server.

    A known app-server agent starts with its contract's subcommand followed by the configuration overrides and feature
    switches the config carries (``-m o3`` and ``-p work`` of a config written for ``codex exec`` become the override
    they stand for). Everything else is dropped and reported: the approval policy and the sandbox are the host's, set
    with every turn, so a flag that would choose them (or any other flag the server does not take) has no place here.
    In ``api_key`` mode the login the runtime hands the server is kept in memory. A command that is not a known
    app-server agent keeps its args as configured.
    """
    spec = command_driver(command)
    if spec is None or spec.transport != "app_server":
        return AppServerLaunch(list(configured_args), [])
    kept: list[str] = []
    dropped: list[str] = []
    index = 0
    while index < len(configured_args):
        arg = configured_args[index]
        flag, value, width = _flag_and_value(configured_args, index)
        if flag in _APP_SERVER_SETTING_FLAGS and value is not None:
            kept += ["-c", f"{_APP_SERVER_SETTING_FLAGS[flag]}={json.dumps(value)}"]
        elif flag in _APP_SERVER_VALUE_FLAGS and value is not None:
            kept += [flag, value]
        elif arg in _APP_SERVER_SWITCHES:
            kept.append(arg)
            width = 1
        else:
            dropped.append(arg)
            width = 1
        index += width
    args = [*spec.launch_args, *kept]
    if auth_mode == "api_key":
        args.extend(_APP_SERVER_MEMORY_CREDENTIALS)
    return AppServerLaunch(args, dropped)


def _option_units(args: tuple[str, ...]) -> list[tuple[str, ...]]:
    """Split launch args into options: a flag with the values that follow it, or a lone leading word (a subcommand)."""
    units: list[list[str]] = []
    for arg in args:
        if arg.startswith("-") or not units:
            units.append([arg])
        else:
            units[-1].append(arg)
    return [tuple(unit) for unit in units]


def _without_restated(args: list[str], baseline: tuple[str, ...]) -> list[str]:
    """``args`` minus every baseline option it restates (the flag together with its value, wherever it stands)."""
    kept = list(args)
    for unit in _option_units(baseline):
        remaining: list[str] = []
        index = 0
        while index < len(kept):
            if tuple(kept[index : index + len(unit)]) == unit:
                index += len(unit)
            else:
                remaining.append(kept[index])
                index += 1
        kept = remaining
    return kept
