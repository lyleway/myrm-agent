"""Unit tests for the request-scoped security overlays (session presets, session grants, remote admission, /learn authoring)."""

import pytest
from myrm_agent_harness.agent.security.config import read_only_permissions, remote_exposed_permissions
from myrm_agent_harness.agent.security.types import PermissionAction, SecurityConfig

from app.core.security.session_overlay import SESSION_PRESETS, SecurityOverlaySpec, YoloGrant, apply_session_preset
from tests.support.overlay_verdict import verdict_under

ALLOW, ASK, DENY = PermissionAction.ALLOW, PermissionAction.ASK, PermissionAction.DENY

_Call = tuple[str, dict[str, object]]

_EDIT: _Call = ("file_edit_tool", {"path": "/tmp/probe.txt", "old_string": "a", "new_string": "b"})
_SHELL: _Call = ("bash_code_execute_tool", {"command": "rm -rf build"})
_EXTERNAL_AGENT: _Call = ("invoke_acp_agent_tool", {"agent_name": "claude"})
_SKILL: _Call = ("skill_manage_tool", {"action": "create", "name": "s"})
_SEARCH: _Call = ("web_search_tool", {"query": "q"})
_SUBAGENT: _Call = ("delegate_task_tool", {"mode": "x"})


def _restrictions(config: dict[str, object] | None) -> list[object]:
    assert config is not None
    layers = config["restrictions"]
    assert isinstance(layers, list)
    return layers


class TestApplySessionPreset:
    """A preset is one restriction layer plus the settings it turns on; the base config is never changed."""

    @pytest.mark.parametrize("preset", [None, "", "hitl", "nonexistent"])
    def test_no_preset_returns_base_unchanged(self, preset: str | None) -> None:
        base: dict[str, object] = {"permissions": {"*": "ask"}, "autoModeEnabled": False}
        assert apply_session_preset(base, preset) is base

    def test_no_preset_over_nothing_returns_nothing(self) -> None:
        assert apply_session_preset(None, "hitl") is None

    def test_accept_edits_appends_a_layer_and_turns_auto_mode_on(self) -> None:
        base: dict[str, object] = {"yoloModeEnabled": False, "permissions": {"*": "ask"}}
        result = apply_session_preset(base, "accept_edits")
        assert result is not None
        assert result is not base
        assert result["yoloModeEnabled"] is False
        assert result["autoModeEnabled"] is True
        assert result["permissions"] == {"*": "ask"}
        assert _restrictions(result) == [dict(SESSION_PRESETS["accept_edits"].restriction)]

    def test_explore_appends_the_read_only_layer_and_leaves_auto_mode_alone(self) -> None:
        result = apply_session_preset({"yoloModeEnabled": False}, "explore")
        assert result is not None
        assert _restrictions(result) == [read_only_permissions()]
        assert "autoModeEnabled" not in result

    @pytest.mark.parametrize("preset", ["accept_edits", "explore"])
    def test_a_preset_over_nothing_builds_a_new_config(self, preset: str) -> None:
        result = apply_session_preset(None, preset)
        assert len(_restrictions(result)) == 1

    @pytest.mark.parametrize("preset", ["accept_edits", "explore"])
    def test_a_preset_keeps_the_layers_already_there_in_front(self, preset: str) -> None:
        saved: dict[str, object] = {"restrictions": [{"web_search_tool": "deny"}]}
        layers = _restrictions(apply_session_preset(saved, preset))
        assert layers[0] == {"web_search_tool": "deny"}
        assert len(layers) == 2

    def test_a_preset_never_changes_the_base(self) -> None:
        base: dict[str, object] = {"permissions": {"*": "ask"}, "extra": True}
        original = {"permissions": {"*": "ask"}, "extra": True}
        apply_session_preset(base, "accept_edits")
        apply_session_preset(base, "explore")
        assert base == original

    @pytest.mark.parametrize("preset", ["accept_edits", "explore"])
    def test_a_saved_denial_stands_under_a_preset(self, preset: str) -> None:
        saved: dict[str, object] = {"permissions": {"web_search_tool": "deny", "code_interpreter": "deny"}}
        result = apply_session_preset(saved, preset)
        assert verdict_under(result, *_SEARCH) is DENY
        assert verdict_under(result, *_SHELL) is DENY

    @pytest.mark.parametrize("preset", ["accept_edits", "explore"])
    def test_a_saved_allow_list_is_not_widened_by_a_preset(self, preset: str) -> None:
        saved: dict[str, object] = {"permissions": {"*": "deny", "web_search_tool": "allow"}}
        result = apply_session_preset(saved, preset)
        assert verdict_under(result, *_SEARCH) is ALLOW
        assert verdict_under(result, *_EDIT) is DENY

    def test_accept_edits_asks_where_the_saved_config_allows_and_denies_what_it_names(self) -> None:
        saved: dict[str, object] = {"permissions": {"code_interpreter": "allow", "invoke_external_agent": "allow"}}
        result = apply_session_preset(saved, "accept_edits")
        assert verdict_under(result, *_SHELL) is ASK
        assert verdict_under(result, *_EXTERNAL_AGENT) is ASK
        assert verdict_under(result, "browser_manage_tool", {"action": "evaluate"}) is DENY
        assert verdict_under(result, *_SEARCH) is ALLOW

    def test_explore_denies_what_the_saved_config_allows(self) -> None:
        saved: dict[str, object] = {"permissions": {"*": "allow", "file_edit": "allow", "code_interpreter": "allow"}}
        result = apply_session_preset(saved, "explore")
        for call in (_EDIT, _SHELL, _EXTERNAL_AGENT):
            assert verdict_under(result, *call) is DENY
        assert verdict_under(result, *_SEARCH) is ALLOW
        assert verdict_under(result, *_SUBAGENT) is ALLOW

    def test_the_layer_does_not_depend_on_the_order_of_the_saved_keys(self) -> None:
        for permissions in ({"*": "allow", "file_edit": "allow"}, {"file_edit": "allow", "*": "allow"}):
            assert verdict_under(apply_session_preset({"permissions": permissions}, "explore"), *_EDIT) is DENY

    def test_a_preset_applied_twice_judges_like_one(self) -> None:
        once = apply_session_preset({"permissions": {"file_edit": "allow"}}, "explore")
        twice = apply_session_preset(once, "explore")
        assert verdict_under(twice, *_EDIT) is verdict_under(once, *_EDIT) is DENY


class TestSessionPresets:
    """The preset table is well-formed."""

    def test_accept_edits_turns_auto_mode_on(self) -> None:
        assert SESSION_PRESETS["accept_edits"].settings == {"autoModeEnabled": True}

    def test_accept_edits_restricts_only_and_names_no_allow(self) -> None:
        restriction = SESSION_PRESETS["accept_edits"].restriction
        assert set(restriction.values()) <= {"ask", "deny"}
        assert restriction["shell_exec"] == "ask"
        assert restriction["browser_evaluate"] == "deny"

    def test_explore_denies_all_writes(self) -> None:
        restriction = SESSION_PRESETS["explore"].restriction
        for permission in ("file_write", "file_edit", "file_delete", "shell_exec", "code_interpreter"):
            assert restriction[permission] == "deny", permission

    def test_explore_denies_everything_the_readonly_profile_denies(self) -> None:
        profile = {rule.permission for rule in SecurityConfig.readonly().ruleset if rule.action is DENY}
        restriction = SESSION_PRESETS["explore"].restriction
        assert profile <= {permission for permission, action in restriction.items() if action == "deny"}

    def test_explore_does_not_turn_auto_mode_on(self) -> None:
        assert "autoModeEnabled" not in SESSION_PRESETS["explore"].settings

    def test_only_explore_leaves_a_permission_to_a_learn_turn(self) -> None:
        exempt = {name: preset.learn_exempt for name, preset in SESSION_PRESETS.items()}
        assert exempt == {"accept_edits": frozenset(), "explore": frozenset({"skill_manage"})}


_SAVED: dict[str, object] = {
    "yoloModeEnabled": True,
    "permissions": {"*": "allow", "file_edit": "allow", "shell_exec": "ask"},
}


class TestSecurityOverlaySpec:
    def test_empty_spec_returns_base_unchanged(self) -> None:
        assert SecurityOverlaySpec().apply(_SAVED) is _SAVED
        assert SecurityOverlaySpec().apply(None) is None

    def test_explore_denies_what_the_saved_config_allows(self) -> None:
        result = SecurityOverlaySpec(preset="explore").apply(_SAVED)
        assert verdict_under(result, *_EXTERNAL_AGENT) is DENY
        assert verdict_under(_SAVED, *_EXTERNAL_AGENT) is ALLOW

    def test_remote_exposed_denies_and_turns_yolo_off(self) -> None:
        result = SecurityOverlaySpec(trust_zone="remote_exposed", admission_path="tunnel").apply(_SAVED)
        assert result is not None
        assert result["yoloModeEnabled"] is False
        assert _restrictions(result) == [remote_exposed_permissions()]
        assert verdict_under(result, *_SHELL) is DENY
        assert verdict_under(result, *_EXTERNAL_AGENT) is DENY

    def test_local_trusted_request_leaves_the_saved_config_alone(self) -> None:
        spec = SecurityOverlaySpec(trust_zone="local_trusted", admission_path="loopback_direct")
        assert spec.apply(_SAVED) is _SAVED

    def test_a_learn_turn_under_explore_leaves_skill_manage_to_the_saved_config(self) -> None:
        spec = SecurityOverlaySpec(preset="explore", learn_authoring=True)
        assert verdict_under(spec.apply(None), *_SKILL) is ASK
        assert verdict_under(SecurityOverlaySpec(preset="explore").apply(None), *_SKILL) is DENY
        assert verdict_under(spec.apply(_SAVED), *_EDIT) is DENY

    def test_a_learn_turn_does_not_reopen_a_saved_denial_of_skill_manage(self) -> None:
        saved: dict[str, object] = {"permissions": {"skill_manage": "deny"}}
        for spec in (SecurityOverlaySpec(learn_authoring=True), SecurityOverlaySpec(preset="explore", learn_authoring=True)):
            assert verdict_under(spec.apply(saved), *_SKILL) is DENY

    def test_a_learn_turn_does_not_reopen_skill_manage_for_a_remote_request(self) -> None:
        spec = SecurityOverlaySpec(trust_zone="remote_exposed", admission_path="tunnel", learn_authoring=True)
        assert verdict_under(spec.apply(None), *_SKILL) is DENY

    def test_every_overlay_stands_whatever_the_order_they_were_written_in(self) -> None:
        spec = SecurityOverlaySpec(
            preset="accept_edits", trust_zone="remote_exposed", admission_path="tunnel", learn_authoring=True
        )
        result = spec.apply(_SAVED)
        assert len(_restrictions(result)) == 2
        assert verdict_under(result, *_SUBAGENT) is DENY  # accept_edits leaves it alone; the remote layer denies it
        assert verdict_under(result, *_SKILL) is DENY
        assert verdict_under(result, *_SHELL) is DENY

    def test_serialization_round_trip_keeps_every_field(self) -> None:
        spec = SecurityOverlaySpec(
            preset="explore",
            trust_zone="remote_exposed",
            admission_path="tunnel",
            learn_authoring=True,
            yolo=YoloGrant(enabled_at=1_700_000_000.5, timeout_seconds=600),
            approval_window_seconds=1800,
        )
        assert SecurityOverlaySpec.model_validate(spec.model_dump(mode="json")) == spec

    def test_spec_is_immutable(self) -> None:
        with pytest.raises(ValueError):
            SecurityOverlaySpec().preset = "explore"

    def test_applying_again_over_a_fresh_copy_of_the_saved_config_adds_one_layer_per_overlay(self) -> None:
        """Every turn reloads the saved config and applies the overlays over it, so layers never pile up."""
        spec = SecurityOverlaySpec(preset="explore", trust_zone="remote_exposed", admission_path="tunnel")
        first = spec.apply(dict(_SAVED))
        second = spec.apply(dict(_SAVED))
        assert first == second
        assert len(_restrictions(second)) == 2


class TestSessionGrants:
    """A channel turn's own YOLO grant and approval window are overlays too: layered over the saved config on every reload."""

    def test_a_yolo_grant_turns_yolo_on_with_its_own_timer(self) -> None:
        spec = SecurityOverlaySpec(yolo=YoloGrant(enabled_at=1_700_000_000.0, timeout_seconds=600))
        assert spec.apply(None) == {
            "yolo_mode_enabled": True,
            "yolo_mode_enabled_at": 1_700_000_000.0,
            "yolo_mode_timeout": 600,
        }

    def test_a_yolo_grant_without_a_timeout_has_no_time_limit(self) -> None:
        result = SecurityOverlaySpec(yolo=YoloGrant(enabled_at=1.0)).apply(None)
        assert result is not None
        assert result["yolo_mode_enabled"] is True
        assert result["yolo_mode_timeout"] is None

    def test_a_yolo_grant_replaces_a_timer_the_saved_config_carries(self) -> None:
        saved: dict[str, object] = {
            "yolo_mode_enabled": True,
            "yolo_mode_enabled_at": 1.0,
            "yolo_mode_timeout": 60,
            "permissions": {"shell_exec": "ask"},
        }
        result = SecurityOverlaySpec(yolo=YoloGrant(enabled_at=2.0)).apply(saved)
        assert result is not None
        assert result["yolo_mode_enabled_at"] == 2.0
        assert result["yolo_mode_timeout"] is None
        assert result["permissions"] == {"shell_exec": "ask"}

    def test_an_approval_window_replaces_the_saved_one_and_nothing_else(self) -> None:
        saved: dict[str, object] = {"approvalTimeoutSeconds": 120, "approvalTimeoutBehavior": "deny"}
        result = SecurityOverlaySpec(approval_window_seconds=1800).apply(saved)
        assert result == {"approvalTimeoutSeconds": 1800, "approvalTimeoutBehavior": "deny"}

    def test_grants_never_mutate_the_saved_config(self) -> None:
        saved: dict[str, object] = {"approvalTimeoutSeconds": 120}
        SecurityOverlaySpec(yolo=YoloGrant(enabled_at=1.0), approval_window_seconds=1800).apply(saved)
        assert saved == {"approvalTimeoutSeconds": 120}

    def test_a_remote_exposed_request_turns_a_session_yolo_grant_off(self) -> None:
        spec = SecurityOverlaySpec(
            yolo=YoloGrant(enabled_at=1.0),
            trust_zone="remote_exposed",
            admission_path="tunnel",
        )
        result = spec.apply(None)
        assert result is not None
        assert result["yoloModeEnabled"] is False
        assert result["yolo_mode_enabled"] is False

    @pytest.mark.parametrize("seconds", [0, -1])
    def test_a_grant_or_window_that_is_not_positive_cannot_be_built(self, seconds: int) -> None:
        with pytest.raises(ValueError):
            YoloGrant(enabled_at=1.0, timeout_seconds=seconds)
        with pytest.raises(ValueError):
            SecurityOverlaySpec(approval_window_seconds=seconds)
