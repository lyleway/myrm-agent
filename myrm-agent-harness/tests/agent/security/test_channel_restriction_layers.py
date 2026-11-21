"""The assembled channel config: presets, the agent's rules and a request's restrictions are layers over the user's rules."""

from __future__ import annotations

from myrm_agent_harness.agent.security.channel_presets import (
    _UNDECLARED_CRON_FILE_DENIALS,
    CHANNEL_PRESETS,
    ChannelType,
    build_channel_security_config,
    get_local_browser_relaxation,
)
from myrm_agent_harness.agent.security.config import (
    read_only_permissions,
    remote_exposed_permissions,
    with_restriction,
)
from myrm_agent_harness.agent.security.engine import evaluate_tool_call, merge
from myrm_agent_harness.agent.security.restrictions import restriction_layer
from myrm_agent_harness.agent.security.types import (
    DEFAULT_RULESET,
    PermissionAction,
    PermissionRule,
)

ALLOW, ASK, DENY = PermissionAction.ALLOW, PermissionAction.ASK, PermissionAction.DENY


def _action(
    permission: str,
    tool_input: dict[str, object] | None = None,
    *,
    channel: str = "web_chat",
    user: dict[str, object] | None = None,
    agent: dict[str, object] | None = None,
    tool_name: str | None = None,
) -> PermissionAction:
    config = build_channel_security_config(channel, user, agent_security_raw=agent)
    return evaluate_tool_call(permission, tool_input or {}, config, tool_name=tool_name)[0]


class TestWithoutAnyOverlayTheLayersAreTheChannelsOwn:
    def test_web_chat_has_no_layer(self) -> None:
        config = build_channel_security_config("web_chat")
        assert config.ruleset == DEFAULT_RULESET
        assert config.restrictions == ()

    def test_local_mode_relaxes_the_baseline_and_adds_no_layer(self) -> None:
        config = build_channel_security_config("web_chat", local_mode=True)
        assert config.ruleset == merge(DEFAULT_RULESET, get_local_browser_relaxation())
        assert config.restrictions == ()

    def test_an_im_channel_adds_its_preset_as_one_layer(self) -> None:
        config = build_channel_security_config("telegram")
        assert config.ruleset == DEFAULT_RULESET
        assert config.restrictions == (restriction_layer(CHANNEL_PRESETS[ChannelType.IM].ruleset),)

    def test_an_undeclared_cron_job_adds_its_preset_and_the_file_denials(self) -> None:
        config = build_channel_security_config("cron")
        assert config.ruleset == DEFAULT_RULESET
        assert config.restrictions == (
            restriction_layer(CHANNEL_PRESETS[ChannelType.CRON].ruleset),
            restriction_layer(_UNDECLARED_CRON_FILE_DENIALS),
        )

    def test_a_cron_job_that_declares_file_write_adds_only_its_preset(self) -> None:
        config = build_channel_security_config("cron", declared_capabilities=("file_write",))
        assert config.restrictions == (restriction_layer(CHANNEL_PRESETS[ChannelType.CRON].ruleset),)

    def test_the_users_rules_stay_in_the_ruleset(self) -> None:
        config = build_channel_security_config("telegram", {"permissions": {"shell_exec": "ask"}})
        assert config.ruleset == (*DEFAULT_RULESET, PermissionRule("shell_exec", "*", ASK))
        assert config.restrictions == (restriction_layer(CHANNEL_PRESETS[ChannelType.IM].ruleset),)


class TestAChannelPresetOnlyTightens:
    def test_a_saved_denial_is_not_loosened_to_the_presets_ask(self) -> None:
        saved: dict[str, object] = {"permissions": {"code_interpreter": "deny"}}
        assert _action("code_interpreter", {"command": "echo hi"}, channel="telegram", user=saved) is DENY

    def test_a_saved_allow_is_asked_about_on_an_im_channel(self) -> None:
        saved: dict[str, object] = {"permissions": {"mcp_invoke": "allow"}}
        assert _action("mcp_invoke", channel="telegram", user=saved, tool_name="mcp__a__b") is ASK
        assert _action("mcp_invoke", channel="web_chat", user=saved, tool_name="mcp__a__b") is ALLOW

    def test_a_saved_allow_of_one_tool_does_not_lift_the_channels_ask(self) -> None:
        saved: dict[str, object] = {"permissions": {"mcp__gmail__send_email": "allow"}}
        assert _action("mcp_invoke", channel="telegram", user=saved, tool_name="mcp__gmail__send_email") is ASK

    def test_a_saved_allow_of_file_writes_is_asked_about_on_an_im_channel(self) -> None:
        saved: dict[str, object] = {"permissions": {"file_write_tool": "allow"}}
        action = _action(
            "file_write", {"path": "/tmp/a.md"}, channel="telegram", user=saved, tool_name="file_write_tool"
        )
        assert action is ASK


class TestARequestsRestrictionsTravelThroughTheAssembly:
    def test_a_layer_in_the_saved_config_stands_on_every_channel(self) -> None:
        raw = with_restriction(None, read_only_permissions())
        for channel in ("web_chat", "telegram", "cron"):
            assert _action("code_interpreter", {"command": "ls"}, channel=channel, user=raw) is DENY

    def test_the_request_layers_come_before_the_channel_layer(self) -> None:
        raw = with_restriction(None, {"file_write": "ask"})
        config = build_channel_security_config("telegram", raw)
        assert config.restrictions == (
            restriction_layer((PermissionRule("file_write", "*", ASK),)),
            restriction_layer(CHANNEL_PRESETS[ChannelType.IM].ruleset),
        )

    def test_a_request_layer_is_not_loosened_by_a_saved_allow(self) -> None:
        raw = with_restriction({"permissions": {"*": "allow", "skill_manage": "allow"}}, remote_exposed_permissions())
        assert _action("skill_manage", user=raw) is DENY

    def test_a_layer_in_the_agents_own_config_stands(self) -> None:
        agent = with_restriction(None, {"spawn_subagent": "deny"})
        assert _action("spawn_subagent", agent=agent) is DENY

    def test_a_layer_of_the_user_and_one_of_the_agent_both_stand(self) -> None:
        user = with_restriction({"permissions": {"*": "allow"}}, {"file_write": "ask"})
        agent = with_restriction(None, {"spawn_subagent": "deny"})
        assert _action("spawn_subagent", user=user, agent=agent) is DENY
        assert _action("file_write", {"path": "/tmp/a.md"}, user=user, agent=agent, tool_name="file_write_tool") is ASK

    def test_the_request_layer_keeps_every_denial_it_was_written_with(self) -> None:
        raw = with_restriction(None, read_only_permissions())
        config = build_channel_security_config("cron", raw, declared_capabilities=("file_write",))
        denied = {rule.permission for rule in config.restrictions[0] if rule.action is DENY}
        assert denied >= {permission for permission, action in read_only_permissions().items() if action == "deny"}


class TestTheAgentsRulesAreALayerOverTheUsers:
    def test_an_agent_denial_stands_over_a_saved_allow(self) -> None:
        user: dict[str, object] = {"permissions": {"code_interpreter": "allow"}}
        agent: dict[str, object] = {"permissions": {"code_interpreter": "deny"}}
        assert _action("code_interpreter", {"command": "make"}, user=user, agent=agent) is DENY

    def test_an_agent_allow_does_not_lift_a_saved_ask(self) -> None:
        user: dict[str, object] = {"permissions": {"spawn_subagent": "ask"}}
        agent: dict[str, object] = {"permissions": {"spawn_subagent": "allow"}}
        assert _action("spawn_subagent", user=user, agent=agent) is ASK

    def test_an_agent_without_a_user_config_is_used_as_it_is(self) -> None:
        """No saved config to hold the agent to: its rules are the ruleset, as the business layer set them."""
        agent: dict[str, object] = {"permissions": {"spawn_subagent": "allow", "code_interpreter": "deny"}}
        assert _action("spawn_subagent", agent=agent) is ALLOW
        assert _action("code_interpreter", {"command": "make"}, agent=agent) is DENY
