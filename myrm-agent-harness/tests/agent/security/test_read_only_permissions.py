"""``read_only_permissions``: the permission keys of a read-only chat session."""

from __future__ import annotations

from myrm_agent_harness.agent.security.config import read_only_permissions
from myrm_agent_harness.agent.security.types import PermissionAction, SecurityConfig
from myrm_agent_harness.core.security.tool_registry import PERSISTENT_STATE_MUTATOR_TOOLS
from myrm_agent_harness.core.security.tool_registry.governance import AUTO_APPROVED_BUILTIN_TOOLS

ALLOW, DENY = PermissionAction.ALLOW.value, PermissionAction.DENY.value

# The built-in tools that run without approval and show the person an effect, yet stay available in a read-only
# session, each with the review that put it here. A tool that is not on this list and not denied fails the gate
# below until someone decides which it is.
_STAYS_AVAILABLE_IN_A_READ_ONLY_SESSION: dict[str, str] = {
    "ask_question_tool": "only asks the person a question",
    "browser_ask_human_tool": "only asks the person to take over a page",
    "kanban_attach": "kanban worker tool: reports on the one task the worker is bound to",
    "kanban_block": "kanban worker tool: reports on the one task the worker is bound to",
    "kanban_comment": "kanban worker tool: reports on the one task the worker is bound to",
    "kanban_complete": "kanban worker tool: reports on the one task the worker is bound to",
    "kanban_heartbeat": "kanban worker tool: reports on the one task the worker is bound to",
}


def test_it_denies_everything_the_readonly_profile_denies() -> None:
    profile = {rule.permission for rule in SecurityConfig.readonly().ruleset if rule.action is PermissionAction.DENY}
    denied = {permission for permission, action in read_only_permissions().items() if action == DENY}
    assert profile <= denied


def test_it_denies_the_state_changing_types_the_profile_leaves_to_its_wildcard() -> None:
    permissions = read_only_permissions()
    assert permissions["knowledge_write"] == DENY
    assert permissions["desktop_control"] == DENY


def test_it_denies_every_tool_whose_writes_outlive_the_session() -> None:
    permissions = read_only_permissions()
    assert {name: permissions.get(name) for name in PERSISTENT_STATE_MUTATOR_TOOLS} == dict.fromkeys(
        PERSISTENT_STATE_MUTATOR_TOOLS, DENY
    )


def test_it_names_no_wildcard_that_would_reopen_the_default_rules() -> None:
    assert "*" not in read_only_permissions()


def test_it_leaves_subagents_available() -> None:
    assert read_only_permissions()["spawn_subagent"] == ALLOW


def test_every_user_visible_builtin_tool_is_denied_or_reviewed() -> None:
    """The built-ins the baseline approves for their visible effects are denied here, or reviewed to stay."""
    user_visible = {name for name, reason in AUTO_APPROVED_BUILTIN_TOOLS.items() if reason == "user_visible"}
    denied = {name for name, action in read_only_permissions().items() if action == DENY}
    assert user_visible - denied == set(_STAYS_AVAILABLE_IN_A_READ_ONLY_SESSION)
