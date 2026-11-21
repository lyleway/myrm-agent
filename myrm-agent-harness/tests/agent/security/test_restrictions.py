"""Restriction layers: each is judged on its own and the strictest verdict of the ruleset and every layer stands."""

from __future__ import annotations

import itertools
import logging
from collections.abc import Mapping
from pathlib import Path

import pytest

from myrm_agent_harness.agent.security.channel_presets import CHANNEL_PRESETS, ChannelType
from myrm_agent_harness.agent.security.config import (
    parse_security_config,
    read_only_permissions,
    remote_exposed_permissions,
    with_restriction,
)
from myrm_agent_harness.agent.security.engine import evaluate, evaluate_tool_call
from myrm_agent_harness.agent.security.path_rules import ACTION_SEVERITY
from myrm_agent_harness.agent.security.restrictions import DENY_EVERYTHING, restriction_layer
from myrm_agent_harness.agent.security.types import (
    DEFAULT_RULESET,
    PermissionAction,
    PermissionRule,
    PermissionRuleset,
    SecurityConfig,
)

ALLOW, ASK, DENY = PermissionAction.ALLOW, PermissionAction.ASK, PermissionAction.DENY
_ACTIONS = (ALLOW, ASK, DENY)


def _rule(permission: str, action: PermissionAction, pattern: str = "*") -> PermissionRule:
    return PermissionRule(permission, pattern, action)


def _layered(ruleset: PermissionRuleset, *layers: PermissionRuleset) -> SecurityConfig:
    return SecurityConfig(ruleset=ruleset, restrictions=layers)


def _action(config: SecurityConfig, permission: str, tool_input: dict[str, object] | None = None) -> PermissionAction:
    return evaluate_tool_call(permission, tool_input or {}, config)[0]


class TestRestrictionLayer:
    def test_a_layer_starts_from_an_allow_all_base(self) -> None:
        rules = (_rule("shell_exec", DENY),)
        assert restriction_layer(rules) == (_rule("*", ALLOW), *rules)

    def test_a_layer_says_nothing_about_a_permission_no_rule_names(self) -> None:
        layer = restriction_layer((_rule("shell_exec", DENY),))
        assert evaluate("web_search", "*", layer).action is ALLOW
        assert evaluate("shell_exec", "ls", layer).action is DENY

    @pytest.mark.parametrize("permission", ["shell_exec", "file_read", "web_search", "any_tool_name"])
    def test_deny_everything_denies_every_permission(self, permission: str) -> None:
        assert evaluate(permission, "*", DENY_EVERYTHING).action is DENY


class TestTheStrictestVerdictStands:
    @pytest.mark.parametrize(("main", "layer"), list(itertools.product(_ACTIONS, _ACTIONS)))
    def test_a_call_gets_the_stricter_of_the_ruleset_and_a_layer(
        self, main: PermissionAction, layer: PermissionAction
    ) -> None:
        config = _layered((_rule("spawn_subagent", main),), restriction_layer((_rule("spawn_subagent", layer),)))
        assert _action(config, "spawn_subagent") is max(main, layer, key=ACTION_SEVERITY.__getitem__)

    def test_a_layer_that_allows_cannot_reopen_what_the_ruleset_denies(self) -> None:
        config = _layered((_rule("code_interpreter", DENY),), restriction_layer((_rule("code_interpreter", ALLOW),)))
        assert _action(config, "code_interpreter", {"command": "ls"}) is DENY

    def test_layers_do_not_loosen_each_other(self) -> None:
        layers = [restriction_layer((_rule("spawn_subagent", action),)) for action in _ACTIONS]
        for ordered in itertools.permutations(layers):
            assert _action(_layered((_rule("*", ALLOW),), *ordered), "spawn_subagent") is DENY

    def test_a_layer_can_allow_inside_its_own_denial(self) -> None:
        """Within one layer the last matching rule wins, so a layer's own carve-out stays possible."""
        layer = restriction_layer((_rule("code_interpreter", DENY), _rule("code_interpreter", ASK, "git *")))
        config = _layered((_rule("*", ALLOW),), layer)
        assert _action(config, "code_interpreter", {"command": "git status"}) is ASK
        assert _action(config, "code_interpreter", {"command": "make"}) is DENY

    def test_a_layer_names_a_tool_as_the_tool_rules_do(self) -> None:
        layer = restriction_layer((_rule("mcp__gmail__send_email", DENY),))
        config = _layered((_rule("*", ALLOW),), layer)
        assert _action(config, "mcp_invoke", {}) is ALLOW
        assert evaluate_tool_call("mcp_invoke", {}, config, tool_name="mcp__gmail__send_email")[0] is DENY
        assert evaluate_tool_call("mcp_invoke", {}, config, tool_name="mcp__gmail__read_inbox")[0] is ALLOW

    def test_a_layer_denial_is_reported_like_a_ruleset_denial(self) -> None:
        config = _layered((_rule("*", ALLOW),), restriction_layer((_rule("skill_manage", DENY),)))
        action, reason = evaluate_tool_call("skill_manage", {}, config, tool_name="skill_manage_tool")
        assert action is DENY
        assert reason == "Permission 'skill_manage_tool' denied by policy"


_MAIN_RULESETS = {
    "default": DEFAULT_RULESET,
    "allow_all": SecurityConfig.full_access().ruleset,
    "workspace": SecurityConfig.workspace(allowed_roots=("/tmp",)).ruleset,
    "read_only": SecurityConfig.readonly().ruleset,
}


def _layer_of(permissions: Mapping[str, str]) -> PermissionRuleset:
    """The layer the parser builds from a permission map, as a request's restriction reaches the engine."""
    config = parse_security_config(with_restriction(None, permissions))
    assert config is not None
    return config.restrictions[0]


_LAYERS = {
    "read_only": _layer_of(read_only_permissions()),
    "remote_exposed": _layer_of(remote_exposed_permissions()),
    "im_channel": restriction_layer(CHANNEL_PRESETS[ChannelType.IM].ruleset),
    "deny_everything": DENY_EVERYTHING,
}
_CALLS: tuple[tuple[str, dict[str, object], str | None], ...] = (
    ("shell_exec", {"data": "ls"}, None),
    ("code_interpreter", {"command": "ls"}, None),
    ("code_interpreter", {"command": "rm -rf build"}, None),
    ("code_interpreter", {"command": "git push"}, None),
    ("mcp_invoke", {}, "mcp__gmail__send_email"),
    ("spawn_subagent", {}, None),
    ("invoke_external_agent", {}, None),
    ("skill_manage", {}, None),
    ("cron_manage", {}, None),
    ("browser_fill", {}, None),
    ("web_search", {}, None),
    ("memory_save_tool", {}, "memory_save_tool"),
)


def _call_text(call: tuple[str, dict[str, object], str | None]) -> str:
    _, tool_input, tool_name = call
    return str(tool_input.get("command") or tool_input.get("data") or tool_name or "")


def _call(config: SecurityConfig, call: tuple[str, dict[str, object], str | None]) -> PermissionAction:
    permission, tool_input, tool_name = call
    return evaluate_tool_call(permission, tool_input, config, tool_name=tool_name)[0]


@pytest.mark.parametrize("call", _CALLS, ids=lambda call: f"{call[0]}:{_call_text(call)}")
@pytest.mark.parametrize("layer_name", list(_LAYERS))
@pytest.mark.parametrize("main_name", list(_MAIN_RULESETS))
def test_adding_a_layer_never_loosens_a_verdict(
    main_name: str, layer_name: str, call: tuple[str, dict[str, object], str | None]
) -> None:
    main = _MAIN_RULESETS[main_name]
    without = _call(SecurityConfig(ruleset=main), call)
    with_layer = _call(_layered(main, _LAYERS[layer_name]), call)
    assert ACTION_SEVERITY[with_layer] >= ACTION_SEVERITY[without]


@pytest.mark.parametrize("call", _CALLS, ids=lambda call: f"{call[0]}:{_call_text(call)}")
def test_a_layer_of_allows_changes_no_verdict(call: tuple[str, dict[str, object], str | None]) -> None:
    layer = restriction_layer(tuple(_rule(permission, ALLOW) for permission, _, _ in _CALLS))
    for main in _MAIN_RULESETS.values():
        assert _call(_layered(main, layer), call) is _call(SecurityConfig(ruleset=main), call)


class TestTheReadOnlyClassifierFallback:
    """A safe command that the ruleset asks about may still be allowed, unless a verdict that asks is final."""

    def test_a_layer_that_asks_as_the_ruleset_does_leaves_a_safe_command_allowed(self) -> None:
        config = _layered(DEFAULT_RULESET, restriction_layer((_rule("code_interpreter", ASK),)))
        verdict = evaluate_tool_call("code_interpreter", {"command": "ls"}, config)
        assert verdict == (ALLOW, "Safe read-only command auto-allow")

    def test_a_pattern_ask_of_a_layer_is_final(self) -> None:
        config = _layered(DEFAULT_RULESET, restriction_layer((_rule("code_interpreter", ASK, "ls*"),)))
        assert _action(config, "code_interpreter", {"command": "ls"}) is ASK

    def test_a_pattern_ask_of_the_ruleset_stays_final_beside_a_layer(self) -> None:
        config = _layered(
            (*DEFAULT_RULESET, _rule("code_interpreter", ASK, "ls*")),
            restriction_layer((_rule("code_interpreter", ASK),)),
        )
        assert _action(config, "code_interpreter", {"command": "ls"}) is ASK

    def test_a_layer_that_denies_a_command_of_a_chain_denies_the_chain(self) -> None:
        config = _layered(DEFAULT_RULESET, restriction_layer((_rule("code_interpreter", DENY, "rm *"),)))
        assert _action(config, "code_interpreter", {"command": "cd build && rm -rf out"}) is DENY
        assert _action(config, "code_interpreter", {"command": "ls"}) is ALLOW


_SPELLINGS_OF_THE_PRIVATE_PLAN = [
    "private/plan.md",
    "./private/plan.md",
    "private//plan.md",
    "docs/../private/plan.md",
    "/workspace/private/plan.md",
    "<ws>/private/plan.md",
]


class TestALayerThatNamesAPlaceGovernsTheFileHoweverItIsSpelled:
    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        root = tmp_path / "ws"
        (root / "private").mkdir(parents=True)
        (root / "docs").mkdir()
        return root

    @staticmethod
    def _read(config: SecurityConfig, workspace: Path, typed: str) -> PermissionAction:
        return evaluate_tool_call(
            "file_read",
            {"paths": [typed.replace("<ws>", str(workspace))]},
            config,
            workspace_root=str(workspace),
            tool_name="file_read_tool",
        )[0]

    @pytest.mark.parametrize("typed", _SPELLINGS_OF_THE_PRIVATE_PLAN)
    def test_a_read_is_denied_in_every_spelling(self, workspace: Path, typed: str) -> None:
        config = _layered(DEFAULT_RULESET, restriction_layer((_rule("file_read", DENY, "private/*"),)))
        assert self._read(config, workspace, typed) is DENY

    @pytest.mark.parametrize("typed", _SPELLINGS_OF_THE_PRIVATE_PLAN)
    def test_a_layer_that_allows_the_place_cannot_reopen_a_denial_of_the_ruleset(
        self, workspace: Path, typed: str
    ) -> None:
        config = _layered(
            (*DEFAULT_RULESET, _rule("file_read", DENY, "private/*")),
            restriction_layer((_rule("file_read", ALLOW, "private/*"),)),
        )
        assert self._read(config, workspace, typed) is DENY

    def test_a_file_the_layer_does_not_name_is_left_to_the_ruleset(self, workspace: Path) -> None:
        config = _layered(DEFAULT_RULESET, restriction_layer((_rule("file_read", DENY, "private/*"),)))
        assert self._read(config, workspace, "docs/readme.md") is ALLOW


class TestParseRestrictions:
    def test_no_restrictions_key_means_no_layers(self) -> None:
        config = parse_security_config({"approvalTimeoutSeconds": 60})
        assert config is not None
        assert config.restrictions == ()

    def test_a_config_of_restrictions_alone_is_a_config(self) -> None:
        config = parse_security_config({"restrictions": [{"file_write": "ask"}]})
        assert config is not None
        assert config.ruleset == DEFAULT_RULESET

    def test_each_entry_is_a_layer_in_order(self) -> None:
        config = parse_security_config({"restrictions": [{"file_write": "ask"}, {"shell_exec": "deny"}]})
        assert config is not None
        assert config.restrictions == (
            restriction_layer((_rule("file_write", ASK),)),
            restriction_layer((_rule("shell_exec", DENY),)),
        )

    def test_the_layers_are_not_part_of_the_ruleset(self) -> None:
        config = parse_security_config({"permissions": {"shell_exec": "ask"}, "restrictions": [{"shell_exec": "deny"}]})
        assert config is not None
        assert config.ruleset == (*DEFAULT_RULESET, _rule("shell_exec", ASK))

    def test_a_layer_reads_like_the_permissions_map(self) -> None:
        config = parse_security_config(
            {"restrictions": [{"delegate_agent": "deny", "knowledge_read": "deny", "file_read": {"*.env": "ask"}}]}
        )
        assert config is not None
        rules = set(config.restrictions[0])
        assert {_rule("spawn_subagent", DENY), _rule("invoke_external_agent", DENY)} <= rules
        assert _rule("wiki_query_tool", DENY) in rules
        assert _rule("file_read", ASK, "*.env") in rules
        assert all(rule.permission != "delegate_agent" for rule in rules)

    @pytest.mark.parametrize("raw", ["deny", {"file_write": "deny"}, 5, True])
    def test_a_value_that_is_not_a_list_denies_every_call(self, raw: object, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            config = parse_security_config({"restrictions": raw})
        assert config is not None
        assert config.restrictions == (DENY_EVERYTHING,)
        assert "denying every call" in caplog.text
        assert _action(config, "web_search") is DENY

    @pytest.mark.parametrize(
        "layer",
        [
            "deny",
            5,
            None,
            ["file_write"],
            {"file_write": "forbid"},
            {"file_write": 5},
            {5: "deny"},
            {"file_read": {"*.env": 5}},
        ],
    )
    def test_a_layer_that_cannot_be_read_denies_every_call(
        self, layer: object, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            config = parse_security_config({"restrictions": [{"file_write": "ask"}, layer]})
        assert config is not None
        assert config.restrictions == (restriction_layer((_rule("file_write", ASK),)), DENY_EVERYTHING)
        assert "Unreadable security restriction layer" in caplog.text
        assert _action(config, "web_search") is DENY

    def test_an_empty_list_is_no_layer(self) -> None:
        config = parse_security_config({"restrictions": [], "approvalTimeoutSeconds": 60})
        assert config is not None
        assert config.restrictions == ()


class TestWithRestriction:
    def test_it_appends_a_layer_and_leaves_the_input_alone(self) -> None:
        raw: dict[str, object] = {"permissions": {"shell_exec": "ask"}}
        result = with_restriction(raw, {"file_write": "deny"})
        assert result == {"permissions": {"shell_exec": "ask"}, "restrictions": [{"file_write": "deny"}]}
        assert raw == {"permissions": {"shell_exec": "ask"}}

    def test_no_config_gives_a_config_of_the_layer(self) -> None:
        assert with_restriction(None, {"file_write": "deny"}) == {"restrictions": [{"file_write": "deny"}]}

    def test_the_layers_already_there_stay_in_front(self) -> None:
        raw: dict[str, object] = {"restrictions": [{"file_write": "ask"}]}
        result = with_restriction(raw, {"shell_exec": "deny"})
        assert result["restrictions"] == [{"file_write": "ask"}, {"shell_exec": "deny"}]
        assert raw == {"restrictions": [{"file_write": "ask"}]}

    def test_the_layer_is_a_copy_of_the_permissions(self) -> None:
        permissions = {"file_write": "deny"}
        result = with_restriction(None, permissions)
        permissions["shell_exec"] = "deny"
        assert result == {"restrictions": [{"file_write": "deny"}]}

    @pytest.mark.parametrize("existing", ["deny", {"file_write": "deny"}, 5])
    def test_a_value_that_is_not_a_list_stays_as_a_layer_that_denies_every_call(self, existing: object) -> None:
        result = with_restriction({"restrictions": existing}, {"file_write": "ask"})
        config = parse_security_config(result)
        assert config is not None
        assert len(config.restrictions) == 2
        assert _action(config, "web_search") is DENY

    def test_the_appended_layer_reads_back_with_every_denial_it_was_written_with(self) -> None:
        config = parse_security_config(with_restriction(None, read_only_permissions()))
        assert config is not None
        assert len(config.restrictions) == 1
        denied = {rule.permission for rule in config.restrictions[0] if rule.action is DENY}
        assert denied >= {permission for permission, action in read_only_permissions().items() if action == "deny"}
        assert _action(config, "code_interpreter", {"command": "ls"}) is DENY

    def test_laying_the_same_layer_twice_changes_no_verdict(self) -> None:
        once = parse_security_config(with_restriction(None, read_only_permissions()))
        twice = parse_security_config(
            with_restriction(with_restriction(None, read_only_permissions()), read_only_permissions())
        )
        assert once is not None
        assert twice is not None
        for call in _CALLS:
            assert _call(twice, call) is _call(once, call)
