"""The gate of a turn the user addresses to one external agent: the verdict the model-initiated call would get, and why."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import pytest
from myrm_agent_harness.agent.security.channel_presets import build_channel_security_config
from myrm_agent_harness.agent.security.types import PermissionAction, SecurityConfig

from app.core.security.session_overlay import SecurityOverlaySpec
from app.services.external_agents.delegation import DenialCause, direct_turn_denial
from tests.support.overlay_verdict import verdict_under

_ALLOW = PermissionAction.ALLOW
_ASK = PermissionAction.ASK
_DENY = PermissionAction.DENY

_EXPLICIT_ALLOW = {"permissions": {"invoke_external_agent": "allow"}}
_REMOTE = SecurityOverlaySpec(trust_zone="remote_exposed", admission_path="tunnel")


@dataclass(frozen=True, kw_only=True)
class _Case:
    """One input the gate must judge: what the user saved, what the agent overrides, what the request lays over both.

    ``verdict`` is what the model-initiated call gets under the same inputs; ``cause`` is why the user's own turn is
    refused (None: it goes through).
    """

    saved: dict[str, object] | None = None
    agent: dict[str, object] | None = None
    overlay: SecurityOverlaySpec = SecurityOverlaySpec()
    verdict: PermissionAction
    cause: DenialCause | None


def _permissions(**actions: str | dict[str, str]) -> dict[str, object]:
    return {"permissions": dict(actions)}


_CASES: dict[str, _Case] = {
    # The seven saved/agent inputs a chat turn can meet.
    "never_saved": _Case(verdict=_ALLOW, cause=None),
    "saved_without_a_rule_for_it": _Case(saved=_permissions(file_write="ask"), verdict=_ALLOW, cause=None),
    "agent_overrides_to_deny": _Case(agent=_permissions(invoke_external_agent="deny"), verdict=_DENY, cause=DenialCause.POLICY),
    "agent_overrides_a_saved_allow_to_deny": _Case(
        saved=_EXPLICIT_ALLOW, agent=_permissions(invoke_external_agent="deny"), verdict=_DENY, cause=DenialCause.POLICY
    ),
    "agent_overrides_to_ask": _Case(agent=_permissions(invoke_external_agent="ask"), verdict=_ASK, cause=None),
    "saved_deny": _Case(saved=_permissions(invoke_external_agent="deny"), verdict=_DENY, cause=DenialCause.POLICY),
    "saved_ask": _Case(saved=_permissions(invoke_external_agent="ask"), verdict=_ASK, cause=None),
    "remote_tools_ask": _Case(saved=_permissions(remote_tools="ask"), verdict=_ASK, cause=None),
    "remote_tools_deny": _Case(saved=_permissions(remote_tools="deny"), verdict=_DENY, cause=DenialCause.POLICY),
    # A rule on the tool's own name is more specific than the permission and governs both entries alike.
    "saved_deny_on_the_tool_name": _Case(
        saved=_permissions(invoke_acp_agent_tool="deny"), verdict=_DENY, cause=DenialCause.POLICY
    ),
    # What the request lays over a saved allow.
    "read_only_session": _Case(
        saved=_EXPLICIT_ALLOW, overlay=SecurityOverlaySpec(preset="explore"), verdict=_DENY, cause=DenialCause.SESSION_PRESET
    ),
    "auto_approve_edits_session": _Case(
        saved=_EXPLICIT_ALLOW, overlay=SecurityOverlaySpec(preset="accept_edits"), verdict=_ASK, cause=None
    ),
    "remote_connection": _Case(saved=_EXPLICIT_ALLOW, overlay=_REMOTE, verdict=_DENY, cause=DenialCause.REMOTE),
    # Several causes at once: the one that no change of the chat's own settings could lift leads.
    "read_only_session_over_a_saved_deny": _Case(
        saved=_permissions(invoke_external_agent="deny"),
        overlay=SecurityOverlaySpec(preset="explore"),
        verdict=_DENY,
        cause=DenialCause.POLICY,
    ),
    "remote_connection_over_a_read_only_session": _Case(
        saved=_EXPLICIT_ALLOW,
        overlay=SecurityOverlaySpec(preset="explore", trust_zone="remote_exposed", admission_path="tunnel"),
        verdict=_DENY,
        cause=DenialCause.REMOTE,
    ),
    "remote_connection_over_a_saved_deny": _Case(
        saved=_permissions(invoke_external_agent="deny"), overlay=_REMOTE, verdict=_DENY, cause=DenialCause.REMOTE
    ),
}


def _config_under(
    saved: dict[str, object] | None, agent: dict[str, object] | None
) -> Callable[[SecurityOverlaySpec], SecurityConfig]:
    """The effective config of a chat turn for any overlay: the production assembly, no stand-ins."""

    def build(overlay: SecurityOverlaySpec) -> SecurityConfig:
        return build_channel_security_config("web_chat", overlay.apply(saved), agent_security_raw=agent)

    return build


@pytest.mark.parametrize("case", _CASES.values(), ids=_CASES.keys())
def test_the_users_own_turn_is_refused_exactly_where_the_model_would_be_denied(case: _Case) -> None:
    model_verdict = verdict_under(
        case.overlay.apply(case.saved), "invoke_acp_agent_tool", {"agent_name": "claude"}, agent_security_raw=case.agent
    )
    assert model_verdict is case.verdict

    denial = direct_turn_denial("claude", case.overlay, _config_under(case.saved, case.agent))

    assert (denial.cause if denial else None) is case.cause
    assert (denial is not None) is (model_verdict is _DENY)  # an ask is the user's own confirmation, a deny stands


def test_a_refusal_carries_the_policy_engines_wording_for_the_log() -> None:
    denial = direct_turn_denial("claude", SecurityOverlaySpec(), _config_under(_permissions(invoke_external_agent="deny"), None))

    assert denial is not None
    assert "invoke_acp_agent_tool" in denial.reason and "denied" in denial.reason


def test_the_agent_named_in_the_turn_does_not_change_the_verdict() -> None:
    config_under = _config_under(_permissions(invoke_external_agent="deny"), None)

    assert direct_turn_denial("codex", SecurityOverlaySpec(), config_under) is not None
    assert direct_turn_denial("some other agent", SecurityOverlaySpec(), config_under) is not None
