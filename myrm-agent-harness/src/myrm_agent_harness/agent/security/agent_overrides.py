"""Per-agent security overrides: an agent can tighten what the user chose, never loosen it.

[INPUT]
- .restrictions::restriction_layer (POS: The layered half of the ruleset: each layer is judged on its own, so none can loosen what the saved rules refuse or ask.)
- .types::AccessRoot, PathPolicy, SecurityConfig, InjectionPolicy, DEFAULT_RULESET (POS: Foundation layer of the security type hierarchy.)

[OUTPUT]
- merge_agent_overrides(): the user's SecurityConfig with one agent's overrides folded in

[POS]
The agent half of the channel assembly in channel_presets.py. The user's own ruleset is left as it is and the
agent's rules become a restriction layer, so an agent's rule can raise a verdict (allow → ask → deny) and never lower
the user's.
"""

from __future__ import annotations

from myrm_agent_harness.agent.security.restrictions import restriction_layer
from myrm_agent_harness.agent.security.types import (
    DEFAULT_RULESET,
    AccessRoot,
    InjectionPolicy,
    PathPolicy,
    PermissionRuleset,
    SecurityConfig,
)


def _own_rules(ruleset: PermissionRuleset, baseline: PermissionRuleset) -> PermissionRuleset:
    """The rules a parsed config added behind the baseline ruleset it was parsed over."""
    return ruleset[len(baseline) :] if ruleset[: len(baseline)] == baseline else ruleset


def merge_agent_overrides(
    user: SecurityConfig | None,
    agent: SecurityConfig | None,
    baseline: PermissionRuleset = DEFAULT_RULESET,
) -> SecurityConfig | None:
    """Merge user-level config with per-agent overrides.

    - capabilities: intersection (Agent restricts, never expands)
    - allowed_roots: union (Agent can grant additional paths)
    - forbidden_paths: always DEFAULT (cannot be overridden)
    - ruleset: with a user config, the user's ruleset stays and the agent's own rules (those behind *baseline*) are
      added as a restriction layer, so they can tighten it and never escalate. Without a user config the agent
      config is used as-is, so untrusted (externally supplied) overrides must be restricted
      by the business layer before they reach this function.
    - restrictions: the user's layers, then the agent's own layers, then the layer of the agent's rules
    - timeout: Agent overrides user if explicitly configured (non-default)
    - yolo_mode: either side enabling YOLO enables it (no user-level ceiling applies)
    """
    if agent is None:
        return user
    if user is None:
        return agent

    agent_positives = frozenset(c for c in agent.capabilities if not c.permission.startswith("!"))
    if agent_positives:
        user_positives = frozenset(c for c in user.capabilities if not c.permission.startswith("!"))
        all_negatives = frozenset(c for c in user.capabilities if c.permission.startswith("!")) | frozenset(
            c for c in agent.capabilities if c.permission.startswith("!")
        )
        caps = (user_positives & agent_positives) | all_negatives
    else:
        caps = user.capabilities

    agent_rules = _own_rules(agent.ruleset, baseline)
    restrictions = (
        *user.restrictions,
        *agent.restrictions,
        *((restriction_layer(agent_rules),) if agent_rules else ()),
    )

    path_set: dict[str, AccessRoot] = {r.path: r for r in user.path_policy.access_roots}
    for root in agent.path_policy.access_roots:
        path_set.setdefault(root.path, root)
    label = agent.path_policy.workspace_label or user.path_policy.workspace_label
    pp = PathPolicy(
        forbidden_paths=user.path_policy.forbidden_paths,
        access_roots=tuple(path_set.values()),
        workspace_label=label,
    )

    timeout = agent.approval_timeout_seconds if agent.approval_timeout_seconds != 120 else user.approval_timeout_seconds
    timeout_behavior = (
        agent.approval_timeout_behavior if agent.approval_timeout_behavior != "deny" else user.approval_timeout_behavior
    )

    network_allowlist = tuple(sorted(set(user.network_allowlist) | set(agent.network_allowlist)))
    network_blocklist = tuple(sorted(set(user.network_blocklist) | set(agent.network_blocklist)))
    command_denylist = tuple(sorted(set(user.command_denylist) | set(agent.command_denylist)))
    domain_hitl_enabled = user.domain_hitl_enabled or agent.domain_hitl_enabled
    auto_mode_enabled = user.auto_mode_enabled or agent.auto_mode_enabled
    auto_review_model = agent.auto_review_model or user.auto_review_model
    auto_review_timeout = min(user.auto_review_timeout_seconds, agent.auto_review_timeout_seconds)
    # YOLO merge: either user globally enables YOLO or agent enables YOLO (e.g. unattended / specialized agent)
    yolo_enabled = user.yolo_mode_enabled or agent.yolo_mode_enabled
    if yolo_enabled:
        if agent.yolo_mode_enabled and user.yolo_mode_enabled:
            yolo_at = agent.yolo_mode_enabled_at or user.yolo_mode_enabled_at
            yolo_timeout = agent.yolo_mode_timeout or user.yolo_mode_timeout
        elif agent.yolo_mode_enabled:
            yolo_at = agent.yolo_mode_enabled_at
            yolo_timeout = agent.yolo_mode_timeout
        else:
            yolo_at = user.yolo_mode_enabled_at
            yolo_timeout = user.yolo_mode_timeout
    else:
        yolo_at = None
        yolo_timeout = None

    injection_policy: InjectionPolicy = "log_only"
    if user.injection_policy == "fail_closed" or agent.injection_policy == "fail_closed":
        injection_policy = "fail_closed"

    return SecurityConfig(
        capabilities=caps,
        ruleset=user.ruleset,
        restrictions=restrictions,
        approval_timeout_seconds=timeout,
        approval_timeout_behavior=timeout_behavior,
        path_policy=pp,
        network_allowlist=network_allowlist,
        network_blocklist=network_blocklist,
        command_denylist=command_denylist,
        domain_hitl_enabled=domain_hitl_enabled,
        injection_policy=injection_policy,
        auto_mode_enabled=auto_mode_enabled,
        auto_review_model=auto_review_model,
        auto_review_timeout_seconds=auto_review_timeout,
        yolo_mode_enabled=yolo_enabled,
        yolo_mode_enabled_at=yolo_at,
        yolo_mode_timeout=yolo_timeout,
    )
