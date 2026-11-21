"""SecurityConfig — the complete security configuration of an agent session.

[INPUT]
- types.rules::CapabilitySet, PermissionAction, PermissionRule, PermissionRuleset, DEFAULT_CAPABILITIES, DEFAULT_RULESET (POS: The vocabulary the engine and every other security type speak.)
- types.paths::PathPolicy, access_roots_from_paths (POS: The path half of a session's security configuration.)
- types.privacy::PrivacyPolicy (POS: How content is classified and what happens to each class.)

[OUTPUT]
- InjectionPolicy: log_only / fail_closed
- SecurityConfig: complete per-session security configuration, with the factory profiles readonly / workspace / full_access / remote_exposed

[POS]
The aggregate every layer of the engine reads. Imports only its sibling type modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from myrm_agent_harness.core.security.types.paths import PathPolicy, _default_path_policy, access_roots_from_paths
from myrm_agent_harness.core.security.types.privacy import PrivacyPolicy, _default_privacy_policy
from myrm_agent_harness.core.security.types.rules import (
    DEFAULT_CAPABILITIES,
    DEFAULT_RULESET,
    CapabilitySet,
    PermissionAction,
    PermissionRule,
    PermissionRuleset,
)

type InjectionPolicy = Literal["log_only", "fail_closed"]


@dataclass(frozen=True, slots=True)
class SecurityConfig:
    """Complete security configuration for an Agent session.

    Evaluation order in ``engine.evaluate_tool_call``:
    1. Capability Fence (deny-by-default, anti-privilege-escalation)
    2a. Shell Command Analyzer (BLOCK → DENY, ESCALATE → ASK)
    2a.5. Command Denylist (user-defined fnmatch globs, YOLO-proof)
    2b. URL Scheme Check (browser_navigate: only http/https allowed)
    2c. Domain HITL (URL-bearing tools: domain not in allowlist → ASK)
    3. Path Policy (forbidden → allowed_roots → workspace, file ops only)
    4. Permission ruleset and restriction layers with target resolution (each last-match-wins, the strictest
       verdict standing)
    5. Risk classifier fallback (SAFE shell commands → ALLOW)
    5.5. Transcript Classifier (all ASK operations, when auto_mode_enabled) → ALLOW/DENY/UNCERTAIN
    6. Fallback to ASK if no rule matches

    ``ruleset`` is one ordered table whose last matching rule wins, so a rule written later can loosen an earlier
    refusal. ``restrictions`` are the rulesets that must not: each layer (``agent.security.restrictions``) is judged
    on its own and the strictest verdict of ``ruleset`` and every layer stands, so a session preset, a channel
    posture, an agent's overrides or a remote admission can only tighten what the saved rules say.
    """

    capabilities: CapabilitySet = field(default=DEFAULT_CAPABILITIES)
    ruleset: PermissionRuleset = field(default=DEFAULT_RULESET)
    restrictions: tuple[PermissionRuleset, ...] = ()
    approval_timeout_seconds: int = 120
    approval_timeout_behavior: str = "deny"
    path_policy: PathPolicy = field(default_factory=_default_path_policy)
    network_allowlist: tuple[str, ...] = ()
    network_blocklist: tuple[str, ...] = ()
    domain_hitl_enabled: bool = True
    privacy_policy: PrivacyPolicy = field(default_factory=_default_privacy_policy)
    auto_mode_enabled: bool = True
    auto_review_model: str | None = None
    auto_review_timeout_seconds: float = 3.0
    transcript_window_size: int = 20
    plan_confirm_enabled: bool = False
    command_denylist: tuple[str, ...] = ()
    injection_policy: InjectionPolicy = "log_only"
    dangerous_intent_policy: Literal["log_only", "fail_closed", "hitl"] = "hitl"
    yolo_mode_enabled: bool = False
    yolo_mode_enabled_at: float | None = None
    yolo_mode_timeout: int | None = None
    classify_all_shell_in_auto_mode: bool = False
    # A scheduled job's config. No person can answer an approval ask in such a run, nor in the agents it delegates
    # to (they inherit this object), so the job's declared capabilities answer instead of a pause.
    is_cron: bool = False

    # ------------------------------------------------------------------
    # Factory methods — pre-built profiles for common security postures
    # ------------------------------------------------------------------

    @classmethod
    def readonly(
        cls,
        *,
        allowed_roots: tuple[str, ...] = (),
        workspace_label: str | None = None,
    ) -> SecurityConfig:
        """Read-only profile: no file writes, no shell, no browser mutations.

        Suitable for data-analysis or research agents that only need to read
        files and browse the web without side effects.
        """
        read_only_rules: PermissionRuleset = (
            PermissionRule("*", "*", PermissionAction.ALLOW),
            PermissionRule("file_write", "*", PermissionAction.DENY),
            PermissionRule("file_edit", "*", PermissionAction.DENY),
            PermissionRule("file_delete", "*", PermissionAction.DENY),
            PermissionRule("shell_exec", "*", PermissionAction.DENY),
            PermissionRule("code_interpreter", "*", PermissionAction.DENY),
            PermissionRule("browser_evaluate", "*", PermissionAction.DENY),
            PermissionRule("browser_fill", "*", PermissionAction.DENY),
            PermissionRule("browser_upload", "*", PermissionAction.DENY),
            PermissionRule("browser_download", "*", PermissionAction.DENY),
            PermissionRule("skill_manage", "*", PermissionAction.DENY),
            PermissionRule("cron_manage", "*", PermissionAction.DENY),
            PermissionRule("channel_notify", "*", PermissionAction.DENY),
            PermissionRule("channel_notify_tool", "*", PermissionAction.DENY),
            PermissionRule("artifact_publish", "*", PermissionAction.DENY),
            PermissionRule("mcp_invoke", "*", PermissionAction.ASK),
            PermissionRule("spawn_subagent", "*", PermissionAction.ALLOW),
            PermissionRule("invoke_external_agent", "*", PermissionAction.DENY),
        )
        return cls(
            ruleset=read_only_rules,
            path_policy=PathPolicy(
                access_roots=access_roots_from_paths(allowed_roots),
                workspace_label=workspace_label,
            ),
        )

    @classmethod
    def workspace(
        cls,
        *,
        allowed_roots: tuple[str, ...],
        shell_action: PermissionAction = PermissionAction.ASK,
        workspace_label: str | None = None,
    ) -> SecurityConfig:
        """Workspace-scoped profile: file ops constrained to *allowed_roots*.

        Shell execution defaults to ASK (human approval required).
        Browser mutations require approval. Network access is unrestricted.
        """
        workspace_rules: PermissionRuleset = (
            PermissionRule("*", "*", PermissionAction.ALLOW),
            PermissionRule("shell_exec", "*", shell_action),
            PermissionRule("code_interpreter", "*", PermissionAction.ASK),
            PermissionRule("browser_evaluate", "*", PermissionAction.DENY),
            PermissionRule("browser_upload", "*", PermissionAction.ASK),
            PermissionRule("browser_download", "*", PermissionAction.ASK),
            PermissionRule("browser_fill", "*", PermissionAction.ASK),
            PermissionRule("skill_manage", "*", PermissionAction.ASK),
            PermissionRule("cron_manage", "*", PermissionAction.ASK),
            PermissionRule("mcp_invoke", "*", PermissionAction.ASK),
            PermissionRule("spawn_subagent", "*", PermissionAction.ALLOW),
            PermissionRule("invoke_external_agent", "*", PermissionAction.ASK),
        )
        return cls(
            ruleset=workspace_rules,
            path_policy=PathPolicy(
                access_roots=access_roots_from_paths(allowed_roots),
                workspace_label=workspace_label,
            ),
        )

    @classmethod
    def full_access(cls) -> SecurityConfig:
        """Full-access profile: all operations allowed, YOLO mode enabled.

        For trusted local environments where the user accepts all risks.
        Equivalent to disabling the security subsystem.
        """
        full_rules: PermissionRuleset = (PermissionRule("*", "*", PermissionAction.ALLOW),)
        return cls(
            ruleset=full_rules,
            yolo_mode_enabled=True,
        )

    @classmethod
    def remote_exposed(cls) -> SecurityConfig:
        """Remote-exposed admission overlay: deny destructive and computer-use tools."""
        remote_rules: PermissionRuleset = (
            PermissionRule("shell_exec", "*", PermissionAction.DENY),
            PermissionRule("code_interpreter", "*", PermissionAction.DENY),
            PermissionRule("desktop_control", "*", PermissionAction.DENY),
            PermissionRule("browser_upload", "*", PermissionAction.DENY),
            PermissionRule("browser_download", "*", PermissionAction.DENY),
            PermissionRule("browser_fill", "*", PermissionAction.DENY),
            PermissionRule("browser_evaluate", "*", PermissionAction.DENY),
            PermissionRule("skill_manage", "*", PermissionAction.DENY),
            PermissionRule("cron_manage", "*", PermissionAction.DENY),
            PermissionRule("mcp_invoke", "*", PermissionAction.ASK),
            PermissionRule("spawn_subagent", "*", PermissionAction.DENY),
            PermissionRule("invoke_external_agent", "*", PermissionAction.DENY),
        )
        return cls(
            ruleset=remote_rules,
        )
