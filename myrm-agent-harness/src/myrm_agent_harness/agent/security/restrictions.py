"""Restriction layers: rulesets that can only tighten what a security config already says.

[INPUT]
- engine::check_capability, evaluate (POS: Layers 1–5 of the security architecture. Deterministic evaluation without side effects.)
- types::PermissionAction, PermissionRule, PermissionRuleset, SecurityConfig (POS: Foundation layer of the security type hierarchy.)

[OUTPUT]
- restriction_layer(): the layer a list of rules makes
- DENY_EVERYTHING: the layer a restriction that cannot be read becomes
- disabled_permissions(): the permission types a config refuses outright
- file_write_disabled(): whether a config refuses ``file_write`` outright

[POS]
The layered half of the ruleset: a session preset, a channel posture, an agent's overrides and a remote admission
each add a layer that the engine judges on its own, so none of them can loosen what the saved rules refuse or ask.
The engine reads the layers (``SecurityConfig.restrictions``); this module builds them and answers which permission
types a config with its layers refuses without looking at a call.
"""

from __future__ import annotations

from collections.abc import Iterable

from myrm_agent_harness.agent.security.engine import check_capability, evaluate
from myrm_agent_harness.agent.security.types import (
    PermissionAction,
    PermissionRule,
    PermissionRuleset,
    SecurityConfig,
)

_ALLOW_ALL = PermissionRule("*", "*", PermissionAction.ALLOW)


def restriction_layer(rules: PermissionRuleset) -> PermissionRuleset:
    """The restriction layer made of *rules*.

    The engine judges a layer on its own, apart from the config's ruleset, and the strictest verdict stands. The
    allow-all base is what a call that no rule names gets from the layer, so a layer says something only where it
    names a permission, and its verdict can raise but never lower what the ruleset or another layer says.
    """
    return (_ALLOW_ALL, *rules)


# A restriction that cannot be read is not dropped, since dropping it would lift a restriction nobody chose to lift.
DENY_EVERYTHING: PermissionRuleset = restriction_layer((PermissionRule("*", "*", PermissionAction.DENY),))


def disabled_permissions(permissions: Iterable[str], config: SecurityConfig) -> frozenset[str]:
    """The permission types *config* refuses outright, whatever the call.

    A permission is disabled when the capability fence does not grant it, or when the ruleset or any restriction
    layer denies it with pattern ``"*"``. Useful for stripping denied tools from the LLM tool list before
    invocation. Callers pass permission types (e.g. ``shell_exec``), not tool names.
    """
    rulesets = (config.ruleset, *config.restrictions)
    return frozenset(
        permission
        for permission in permissions
        if not check_capability(permission, "*", config.capabilities)
        or any(evaluate(permission, "*", ruleset).action == PermissionAction.DENY for ruleset in rulesets)
    )


def file_write_disabled(config: SecurityConfig | None) -> bool:
    """Whether the policy refuses ``file_write`` outright: a run under it cannot save a file however it asks.

    ``None`` and any object that is not a ``SecurityConfig`` carry no policy to read, so they deny nothing.
    """
    return isinstance(config, SecurityConfig) and "file_write" in disabled_permissions(["file_write"], config)
