"""The command line a known agent is started with: one resolver per transport.

[INPUT]
- toolkits.acp.auth._profiles::all_profiles, command_profile, profile_for (POS: Authentication profile registry; resolves a command to its agent contract)

[OUTPUT]
- resolve_cli_args: Merge a CLI agent's baseline launch args with the config args and the permission-mode args.
- AcpLaunch, resolve_acp_args: The launch args of an agent driven over ACP: its contract's entry args plus what the config adds.

[POS]
Sole owner of the permission flags of every known agent. A CLI agent has nobody to ask, so its permission mode is a flag
this module sets; an ACP agent asks the host, so its mode is the one contract flag that makes it ask, and every flag a
config carries that would make it skip asking is dropped. A command that is not a known agent keeps its args as
configured: no contract exists for it and its flags may mean something unrelated.
"""

from __future__ import annotations

from dataclasses import dataclass

from myrm_agent_harness.toolkits.acp.auth._profiles import all_profiles, command_profile, profile_for

# Permission-mode args per CLI backend. Index 0 is the flag name, the remainder is its
# value; both are emitted as separate argv entries so `--flag=value` and `--flag value`
# conventions are avoided on CLIs whose parsers only accept spaced values.
#
# Every value below is taken from the CLI's own documentation:
# - claude: `--permission-mode` accepts default / acceptEdits / plan / auto / dontAsk /
#   bypassPermissions. `default` is the mode that asks before anything beyond a read; a
#   headless run has nobody to ask, so it either hands the question to the host through
#   `--permission-prompt-tool` (see ``CliDialect.with_permission_bridge``) or refuses.
# - codex: the `exec` entry point already hardcodes approval-policy `never` (headless
#   never prompts) and does not accept `--ask-for-approval`, so `-s/--sandbox` is the
#   only lever, and `safe` can only mean read-only.
# The agents driven over ACP (gemini, qwen) have no row: they ask the host in the mode their contract names.
_PERMISSION_MODE_ARGS: dict[str, dict[str, tuple[str, ...]]] = {
    "claude": {
        "safe": ("--permission-mode", "default"),
        "allow_all": ("--permission-mode", "bypassPermissions"),
    },
    "codex": {
        "safe": ("--sandbox", "read-only"),
        "allow_all": ("--sandbox", "workspace-write"),
    },
}

# Permission flags a mapping owns, keyed by backend. Stripping is deliberately
# per-backend: `-s` is codex's sandbox *mode* (`-s read-only`) but gemini's boolean
# sandbox *toggle*, so a global strip would silently disable sandboxing the user asked
# for. Each entry holds the exact flag names, the names whose value is a separate argv
# element, and the per-backend `--flag=value` prefixes — gemini's `--sandbox=true` is the
# user's own toggle and must survive, while codex's `--sandbox=read-only` is the mode this
# mapping owns.
_PERMISSION_FLAGS_BY_BACKEND: dict[str, tuple[frozenset[str], frozenset[str], tuple[str, ...]]] = {
    "claude": (
        # ``--permission-prompt-tool`` is how a headless turn asks the host (``CliDialect.with_permission_bridge``).
        frozenset({"--permission-mode", "--dangerously-skip-permissions", "--full-auto", "--permission-prompt-tool"}),
        frozenset({"--permission-mode", "--permission-prompt-tool"}),
        ("--permission-mode=", "--permission-prompt-tool="),
    ),
    "codex": (
        # Only flags this mapping replaces belong here. Hook trust is a separate concern
        # the mapping has no substitute for, so `--dangerously-bypass-hook-trust` is left
        # alone rather than silently dropped.
        frozenset(
            {
                "-s",
                "--sandbox",
                "--full-auto",
                "--approve-for-me",
                "--yolo",
                "--dangerously-bypass-approvals-and-sandbox",
            }
        ),
        frozenset({"-s", "--sandbox"}),
        ("--sandbox=",),
    ),
    "gemini": (
        frozenset({"-y", "--yolo", "--approval-mode"}),
        frozenset({"--approval-mode"}),
        ("--approval-mode=",),
    ),
    "qwen": (
        frozenset({"-y", "--yolo", "--approval-mode"}),
        frozenset({"--approval-mode"}),
        ("--approval-mode=",),
    ),
}


def _check_tables() -> None:
    """Fail loudly unless the tables cover exactly the agents that need them.

    Every agent needs a strip table, and exactly the CLI agents need a mode mapping: a mode mapping without a strip
    table would resolve to a duplicated flag (a parser error or a silent last-wins override), a strip table without a
    mapping would leave a CLI agent in an unspecified mode, and a mapping on an ACP agent would never be read.
    Evaluated on each resolve so a half-added agent fails at first invocation instead of silently mis-resolving.
    """
    backends = {profile.backend for profile in all_profiles()}
    cli_backends = {profile.backend for profile in all_profiles() if profile.transport == "cli"}
    if set(_PERMISSION_FLAGS_BY_BACKEND) != backends or set(_PERMISSION_MODE_ARGS) != cli_backends:
        msg = (
            "Permission tables are out of sync with _PROFILES: "
            f"profiles={sorted(backends)}, cli_profiles={sorted(cli_backends)}, "
            f"mode_args={sorted(_PERMISSION_MODE_ARGS)}, "
            f"strip_flags={sorted(_PERMISSION_FLAGS_BY_BACKEND)}"
        )
        raise ValueError(msg)


def _strip_permission_flags(backend: str, args: list[str]) -> tuple[list[str], list[str]]:
    """``args`` without the permission flags this backend's mapping owns (and their values), and what was removed."""
    flag_lookup, value_flags, prefix_flags = _PERMISSION_FLAGS_BY_BACKEND[backend]
    kept: list[str] = []
    removed: list[str] = []
    skip_value = False
    for arg in args:
        if skip_value:
            skip_value = False
            removed.append(arg)
            continue
        if arg in flag_lookup or arg.startswith(prefix_flags):
            skip_value = arg in value_flags
            removed.append(arg)
            continue
        kept.append(arg)
    return kept, removed


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
    baseline option (a preset's ``exec --json``) adds nothing; the config's own repeated
    options all stay, since ``-c a=1 -c b=2`` is two settings and not a duplicate.
    """
    profile = profile_for(backend_or_command)
    if profile is None or profile.transport != "cli":
        # Not a CLI agent we know: we own no permission contract for it, so args pass through
        # untouched rather than stripping flags that may mean something else entirely.
        return list(configured_args)

    # Every registered profile must carry permission tables; the assertion fails loudly
    # otherwise, so a half-added backend can never reach the merge below undefined.
    _check_tables()

    mode_args = _PERMISSION_MODE_ARGS[profile.backend].get(permission_mode)
    if mode_args is None:
        # A mode the table does not know would start the CLI under its own permission defaults, which is never what
        # the caller asked for; refusing to launch is the only fail-closed answer.
        msg = f"Unknown permission mode {permission_mode!r}; expected one of {sorted(_PERMISSION_MODE_ARGS[profile.backend])}"
        raise ValueError(msg)
    merged: list[str] = list(mode_args)
    merged.extend(profile.cli_launch_args)
    owned, _ = _strip_permission_flags(profile.backend, configured_args)
    merged.extend(_without_restated(owned, profile.cli_launch_args))
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
    profile = command_profile(command)
    if profile is None or profile.transport != "acp":
        return AcpLaunch(list(configured_args), [])
    _check_tables()
    owned, dropped = _strip_permission_flags(profile.backend, configured_args)
    args = [*profile.acp_launch_args, *_without_restated(owned, profile.acp_launch_args)]
    return AcpLaunch(args, dropped)


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
