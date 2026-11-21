"""The verdict a SecurityConfig gives a (permission, target) pair, for tests of rule semantics.

[INPUT]
- config (POS: the SecurityConfig under test; its ruleset and restriction layers are what the verdict reads)

[OUTPUT]
- verdict(config, permission, target): the strictest action of the config's ruleset and each of its restriction layers

[POS]
Reads a config the way the engine's rules step does, without a tool call to build: the ruleset and every restriction
layer are judged on their own and the strictest action stands. Tests that assert what the rules say about a
permission use it; tests that assert what a call receives go through ``evaluate_tool_call``.
"""

from __future__ import annotations

from myrm_agent_harness.agent.security.engine import evaluate
from myrm_agent_harness.agent.security.path_rules import ACTION_SEVERITY
from myrm_agent_harness.agent.security.types import PermissionAction, SecurityConfig


def verdict(config: SecurityConfig, permission: str, target: str = "*") -> PermissionAction:
    """The action *config* gives (*permission*, *target*): the strictest of its ruleset and every restriction layer."""
    return max(
        (evaluate(permission, target, ruleset).action for ruleset in (config.ruleset, *config.restrictions)),
        key=ACTION_SEVERITY.__getitem__,
    )
