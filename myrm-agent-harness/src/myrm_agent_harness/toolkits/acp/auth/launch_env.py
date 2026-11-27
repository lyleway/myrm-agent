"""The environment every launch of a CLI agent starts from.

[INPUT]
- core.security.process_env::EnvInheritPolicy, sanitize_env (POS: Child process environment isolation SSOT)
- core.security.redact::command_preview (POS: A command shortened and scrubbed for logs and messages)
- toolkits.acp.auth._profiles::command_profile, profile_for (POS: Authentication profile registry.)
- toolkits.acp.toolchains::toolchain_node_bin (POS: The managed Node a CLI installed by the isolated toolchain needs on PATH)

[OUTPUT]
- cli_base_env: The environment a CLI starts from, taken from a source environment.
- account_env: The variables that put a CLI in the home of the account it runs as.

[POS]
Shared baseline of delegated turns and of the probes (``--version``, ``auth status``) that predict them, so a probe
sees what a turn will see.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from myrm_agent_harness.core.security.process_env import EnvInheritPolicy, sanitize_env
from myrm_agent_harness.core.security.redact import command_preview
from myrm_agent_harness.toolkits.acp.auth._profiles import command_profile, profile_for
from myrm_agent_harness.toolkits.acp.toolchains import toolchain_node_bin


def cli_base_env(command: str | None, source: Mapping[str, str]) -> dict[str, str]:
    """The environment every launch of ``command`` starts from, taken from ``source``.

    Only the shared core variables (PATH, HOME, locale, proxy, ...) and the variables that relocate this CLI's own
    login home are inherited: a delegated CLI hands its environment on to every command the model runs, so provider
    keys and the operator's other secrets stay behind. A CLI the isolated toolchain installed also gets that
    toolchain's Node on PATH.
    """
    env = sanitize_env(dict(source), EnvInheritPolicy.CORE)
    profile = profile_for(command) if command else None
    for name in profile.relocation_vars if profile is not None else ():
        if name in source:
            env[name] = source[name]
    node_bin = toolchain_node_bin(command) if command else None
    if node_bin is not None:
        env["PATH"] = f"{node_bin}{os.pathsep}{env.get('PATH', '')}"
    return env


def account_env(command: str | None, account_home: str | None) -> dict[str, str]:
    """The variables that put ``command`` in ``account_home``, the home of the account it runs as; none without one.

    They are applied apart from the agent's configured environment: a subscription-mode backend drops the entries of
    that mapping that carry a provider prefix, and the variable of two of the CLIs (``GEMINI_CLI_HOME``,
    ``QWEN_HOME``) has one.

    Raises:
        ValueError: the home is not an absolute path, or ``command`` is not a known agent: how a CLI is relocated is
            part of that CLI's contract, so any other command has no variable to set.
    """
    if account_home is None:
        return {}
    home = Path(account_home)
    if not home.is_absolute():
        msg = f"account_home must be an absolute path, got {account_home!r}"
        raise ValueError(msg)
    profile = command_profile(command)
    if profile is None:
        msg = f"account_home needs the command of a known agent, got {command_preview(command or '', 120)!r}"
        raise ValueError(msg)
    return profile.isolated_env(home)
