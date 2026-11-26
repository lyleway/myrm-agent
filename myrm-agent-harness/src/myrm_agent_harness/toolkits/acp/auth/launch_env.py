"""The environment every launch of a CLI agent starts from.

[INPUT]
- core.security.process_env::EnvInheritPolicy, sanitize_env (POS: Child process environment isolation SSOT)
- toolkits.acp.auth._profiles::profile_for (POS: Authentication profile registry.)
- toolkits.acp.toolchains::toolchain_node_bin (POS: The managed Node a CLI installed by the isolated toolchain needs on PATH)

[OUTPUT]
- cli_base_env: The environment a CLI starts from, taken from a source environment.

[POS]
Shared baseline of delegated turns and of the probes (``--version``, ``auth status``) that predict them, so a probe
sees what a turn will see.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from myrm_agent_harness.core.security.process_env import EnvInheritPolicy, sanitize_env
from myrm_agent_harness.toolkits.acp.auth._profiles import profile_for
from myrm_agent_harness.toolkits.acp.toolchains import toolchain_node_bin


def cli_base_env(command: str | None, source: Mapping[str, str]) -> dict[str, str]:
    """The environment every launch of ``command`` starts from, taken from ``source``.

    Only the shared core variables (PATH, HOME, locale, proxy, ...) and the variable that relocates this CLI's own
    login home are inherited: a delegated CLI hands its environment on to every command the model runs, so provider
    keys and the operator's other secrets stay behind. A CLI the isolated toolchain installed also gets that
    toolchain's Node on PATH.
    """
    env = sanitize_env(dict(source), EnvInheritPolicy.CORE)
    profile = profile_for(command) if command else None
    home_env = profile.home_env if profile is not None else None
    if home_env and home_env in source:
        env[home_env] = source[home_env]
    node_bin = toolchain_node_bin(command) if command else None
    if node_bin is not None:
        env["PATH"] = f"{node_bin}{os.pathsep}{env.get('PATH', '')}"
    return env
