"""Remote tool policy overlay tests."""

from __future__ import annotations

from myrm_agent_harness.agent.security.config import remote_exposed_permissions
from myrm_agent_harness.agent.security.types import PermissionAction

from app.core.security.session_overlay import apply_session_preset
from app.remote_access.tool_policy import merge_remote_security_overlay
from tests.support.overlay_verdict import verdict_under

ALLOW, ASK, DENY = PermissionAction.ALLOW, PermissionAction.ASK, PermissionAction.DENY

_Call = tuple[str, dict[str, object]]

_SHELL: _Call = ("bash_code_execute_tool", {"command": "rm -rf build"})
_DESKTOP: _Call = ("desktop_interact_tool", {"action": "click"})
_SUBAGENT: _Call = ("delegate_task_tool", {"mode": "x"})
_SEARCH: _Call = ("web_search_tool", {"query": "q"})
_MCP_WRITE: _Call = ("mcp__notion__create_page", {})


def _remote(raw: dict[str, object] | None) -> dict[str, object]:
    merged = merge_remote_security_overlay(raw, trust_zone="remote_exposed", admission_path="public_ingress")
    assert merged is not None
    return merged


def test_local_trusted_leaves_config_unchanged() -> None:
    raw: dict[str, object] = {"permissions": {"shell_exec": "ask"}}
    assert merge_remote_security_overlay(raw, trust_zone="local_trusted", admission_path="loopback_direct") == raw


def test_remote_exposed_denies_destructive_tools() -> None:
    merged = _remote({"permissions": {"shell_exec": "ask"}})
    assert verdict_under(merged, *_SHELL) is DENY
    assert verdict_under(merged, *_DESKTOP) is DENY
    assert verdict_under(merged, *_SEARCH) is ALLOW
    assert merged["yoloModeEnabled"] is False


def test_remote_exposed_adds_one_layer_and_leaves_the_saved_permissions_as_they_were() -> None:
    saved: dict[str, object] = {"permissions": {"shell_exec": "ask"}}
    merged = _remote(saved)
    assert merged["permissions"] == {"shell_exec": "ask"}
    assert merged["restrictions"] == [remote_exposed_permissions()]
    assert saved == {"permissions": {"shell_exec": "ask"}}


def test_remote_exposed_marks_the_request_as_remote() -> None:
    merged = _remote(None)
    assert merged["requestTrustContext"] == {
        "trustZone": "remote_exposed",
        "admissionPath": "public_ingress",
        "restrictDestructiveTools": True,
    }


def test_remote_overlay_does_not_soften_a_saved_denial_of_a_permission_it_names() -> None:
    """The overlay asks about ``mcp_invoke``; a user who denied it keeps the denial."""
    merged = _remote({"permissions": {"mcp_invoke": "deny"}})
    assert verdict_under(merged, *_MCP_WRITE) is DENY


def test_remote_overlay_asks_where_the_saved_config_allows() -> None:
    merged = _remote({"permissions": {"mcp_invoke": "allow"}})
    assert verdict_under(merged, *_MCP_WRITE) is ASK


def test_remote_overlay_after_session_preset_preserves_deny() -> None:
    """Regression: session preset applied BEFORE remote overlay must NOT bypass deny."""
    after_preset = apply_session_preset({"permissions": {"*": "ask"}}, "accept_edits")
    assert verdict_under(after_preset, *_SHELL) is ASK

    final = _remote(after_preset)
    assert verdict_under(final, *_SHELL) is DENY
    assert verdict_under(final, *_DESKTOP) is DENY
    assert final["yoloModeEnabled"] is False


def test_remote_overlay_keeps_a_session_presets_settings() -> None:
    final = _remote(apply_session_preset(None, "accept_edits"))
    assert final["autoModeEnabled"] is True
    layers = final["restrictions"]
    assert isinstance(layers, list)
    assert len(layers) == 2


def test_explore_preset_plus_remote_exposed_both_deny_preserved() -> None:
    """Explore preset denies shell; the remote overlay denies the subagent the preset allows. Both layers stack."""
    after_preset = apply_session_preset({"permissions": {"*": "ask"}}, "explore")
    assert verdict_under(after_preset, *_SHELL) is DENY
    assert verdict_under(after_preset, *_SUBAGENT) is ASK

    final = _remote(after_preset)
    assert verdict_under(final, *_SHELL) is DENY
    assert verdict_under(final, *_SUBAGENT) is DENY
    assert verdict_under(final, *_DESKTOP) is DENY


def test_the_overlay_applied_to_an_already_overlaid_config_judges_the_same() -> None:
    once = _remote({"permissions": {"mcp_invoke": "allow"}})
    twice = _remote(once)
    for call in (_SHELL, _DESKTOP, _SUBAGENT, _SEARCH, _MCP_WRITE):
        assert verdict_under(twice, *call) is verdict_under(once, *call)


def test_device_policy_is_carried_for_a_request_that_is_not_remote() -> None:
    merged = merge_remote_security_overlay(
        {"permissions": {"shell_exec": "ask"}},
        trust_zone="local_trusted",
        admission_path="loopback_direct",
        device_policy={"allow": ["laptop"]},
    )
    assert merged == {"permissions": {"shell_exec": "ask"}, "devicePolicy": {"allow": ["laptop"]}}
