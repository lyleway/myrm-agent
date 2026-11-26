"""Slash-command resolution and argument parsing: a typed command, the approval words, topic arguments and the agent routes."""

from __future__ import annotations

from app.channels.routing.command_defs import (
    CommandAction,
    CommandDef,
    CommandKind,
)
from app.channels.routing.command_registry import (
    CommandRegistry,
)
from app.channels.routing.commands import (
    DenyWithReason,
    TopicCommand,
    is_explicit_approval_command,
    parse_approval_command,
    parse_topic_args,
)

_TEST_AGENT_ROUTES: tuple[CommandDef, ...] = (
    CommandDef(
        name="claude",
        description="Route to Claude",
        kind=CommandKind.AGENT_ROUTE,
        aliases=("cc",),
        agent_id="claude",
        parse_args=True,
        category="Agent",
    ),
    CommandDef(
        name="codex",
        description="Route to Codex",
        kind=CommandKind.AGENT_ROUTE,
        aliases=("cx",),
        agent_id="codex",
        parse_args=True,
        category="Agent",
    ),
    CommandDef(
        name="cursor",
        description="Route to Cursor",
        kind=CommandKind.AGENT_ROUTE,
        aliases=("cs",),
        agent_id="cursor",
        parse_args=True,
        category="Agent",
    ),
    CommandDef(
        name="kimi",
        description="Route to Kimi",
        kind=CommandKind.AGENT_ROUTE,
        aliases=("km",),
        agent_id="kimi",
        parse_args=True,
        category="Agent",
    ),
    CommandDef(
        name="gemini",
        description="Route to Gemini",
        kind=CommandKind.AGENT_ROUTE,
        aliases=("gm",),
        agent_id="gemini",
        parse_args=True,
        category="Agent",
    ),
    CommandDef(
        name="openclaw",
        description="Route to OpenClaw",
        kind=CommandKind.AGENT_ROUTE,
        aliases=("oc",),
        agent_id="openclaw",
        parse_args=True,
        category="Agent",
    ),
    CommandDef(
        name="opencode",
        description="Route to OpenCode",
        kind=CommandKind.AGENT_ROUTE,
        aliases=("ocd",),
        agent_id="opencode",
        parse_args=True,
        category="Agent",
    ),
)


def _make_registry() -> CommandRegistry:
    """Create a registry with test agent routes."""
    registry = CommandRegistry()
    for cmd in _TEST_AGENT_ROUTES:
        registry.register(cmd)
    return registry


class TestCommandResolution:
    def test_stop_exact(self) -> None:
        registry = _make_registry()
        resolved = registry.resolve("/stop")
        assert resolved is not None
        assert resolved.command_def.action == CommandAction.STOP

    def test_stop_case_insensitive(self) -> None:
        registry = _make_registry()
        resolved = registry.resolve("/STOP")
        assert resolved is not None
        assert resolved.command_def.action == CommandAction.STOP

    def test_stop_with_whitespace(self) -> None:
        registry = _make_registry()
        resolved = registry.resolve("  /stop  ")
        assert resolved is not None
        assert resolved.command_def.action == CommandAction.STOP

    def test_not_stop(self) -> None:
        registry = _make_registry()
        assert registry.resolve("/start") is None
        assert registry.resolve("stop") is None

    def test_new_session(self) -> None:
        registry = _make_registry()
        resolved = registry.resolve("/new")
        assert resolved is not None
        assert resolved.command_def.action == CommandAction.NEW_SESSION

    def test_compact(self) -> None:
        registry = _make_registry()
        resolved = registry.resolve("/compact")
        assert resolved is not None
        assert resolved.command_def.action == CommandAction.COMPACT
        assert resolved.raw_args == ""

    def test_compact_with_focus_topic(self) -> None:
        registry = _make_registry()
        resolved = registry.resolve("/compact Focus on API design")
        assert resolved is not None
        assert resolved.command_def.action == CommandAction.COMPACT
        assert resolved.raw_args == "Focus on API design"

    def test_compact_with_chinese_topic(self) -> None:
        registry = _make_registry()
        resolved = registry.resolve("/compact 聚焦API设计决策")
        assert resolved is not None
        assert resolved.raw_args == "聚焦API设计决策"

    def test_help(self) -> None:
        registry = _make_registry()
        resolved = registry.resolve("/help")
        assert resolved is not None
        assert resolved.command_def.action == CommandAction.HELP


class TestParseApprovalCommand:
    def test_approve_once_slash(self) -> None:
        assert parse_approval_command("/approve") == "allow_once"

    def test_approve_once_numeric(self) -> None:
        assert parse_approval_command("1") == "allow_once"

    def test_approve_once_natural_language(self) -> None:
        assert parse_approval_command("y") == "allow_once"
        assert parse_approval_command("yes") == "allow_once"
        assert parse_approval_command("ok") == "allow_once"
        assert parse_approval_command("同意") == "allow_once"
        assert parse_approval_command("好的") == "allow_once"

    def test_approve_always_slash(self) -> None:
        assert parse_approval_command("/approve-always") == "allow_always"
        assert parse_approval_command("/always") == "allow_always"

    def test_approve_always_natural_language(self) -> None:
        assert parse_approval_command("永远允许") == "allow_always"
        assert parse_approval_command("总是允许") == "allow_always"
        assert parse_approval_command("aa") == "allow_always"
        assert parse_approval_command("!y") == "allow_always"

    def test_deny_slash(self) -> None:
        assert parse_approval_command("/deny") == "deny"

    def test_deny_numeric(self) -> None:
        assert parse_approval_command("2") == "deny"

    def test_deny_natural_language(self) -> None:
        assert parse_approval_command("n") == "deny"
        assert parse_approval_command("no") == "deny"
        assert parse_approval_command("拒绝") == "deny"
        assert parse_approval_command("不行") == "deny"

    def test_batch_valid(self) -> None:
        result = parse_approval_command("/batch a,d,a")
        assert result == ["allow_once", "deny", "allow_once"]

    def test_batch_with_reject(self) -> None:
        result = parse_approval_command("/batch r,approve,deny")
        assert result == ["deny", "allow_once", "deny"]

    def test_batch_with_always(self) -> None:
        result = parse_approval_command("/batch aa,a,d")
        assert result == ["allow_always", "allow_once", "deny"]

    def test_batch_empty_spec(self) -> None:
        assert parse_approval_command("/batch ") is None

    def test_batch_invalid_token(self) -> None:
        assert parse_approval_command("/batch a,x,d") is None

    def test_unrecognized(self) -> None:
        assert parse_approval_command("hello") is None

    def test_approve_emoji_thumbs_up(self) -> None:
        assert parse_approval_command("👍") == "allow_once"

    def test_approve_emoji_heart(self) -> None:
        assert parse_approval_command("❤️") == "allow_once"

    def test_approve_emoji_check(self) -> None:
        assert parse_approval_command("✅") == "allow_once"

    def test_approve_emoji_skin_tone(self) -> None:
        assert parse_approval_command("👍🏽") == "allow_once"

    def test_approve_always_emoji_infinity(self) -> None:
        assert parse_approval_command("♾") == "allow_always"
        assert parse_approval_command("♾️") == "allow_always"

    def test_approve_always_emoji_star(self) -> None:
        assert parse_approval_command("⭐") == "allow_always"

    def test_deny_emoji_thumbs_down(self) -> None:
        assert parse_approval_command("👎") == "deny"

    def test_deny_emoji_cross(self) -> None:
        assert parse_approval_command("❌") == "deny"

    def test_deny_emoji_no_entry(self) -> None:
        assert parse_approval_command("🚫") == "deny"

    def test_emoji_not_approval(self) -> None:
        assert parse_approval_command("🎉") is None


class TestIsExplicitApprovalCommand:
    def test_approve(self) -> None:
        assert is_explicit_approval_command("/approve") is True

    def test_approve_always(self) -> None:
        assert is_explicit_approval_command("/approve-always") is True
        assert is_explicit_approval_command("/always") is True

    def test_deny(self) -> None:
        assert is_explicit_approval_command("/deny") is True

    def test_batch(self) -> None:
        assert is_explicit_approval_command("/batch a,d") is True

    def test_numeric_not_explicit(self) -> None:
        assert is_explicit_approval_command("1") is False


class TestDenyWithReason:
    """Tests for /deny <reason> parsing returning DenyWithReason."""

    def test_deny_with_reason_returns_dataclass(self) -> None:
        result = parse_approval_command("/deny please use a safer approach")
        assert isinstance(result, DenyWithReason)
        assert result.reason == "please use a safer approach"

    def test_deny_with_reason_case_insensitive(self) -> None:
        result = parse_approval_command("/Deny too risky")
        assert isinstance(result, DenyWithReason)
        assert result.reason == "too risky"

    def test_deny_with_reason_is_explicit(self) -> None:
        assert is_explicit_approval_command("/deny please stop") is True

    def test_deny_with_reason_whitespace(self) -> None:
        result = parse_approval_command("  /deny  some reason  ")
        assert isinstance(result, DenyWithReason)
        assert result.reason == "some reason"
        assert is_explicit_approval_command("  /deny  some reason  ") is True

    def test_deny_without_reason_returns_literal(self) -> None:
        assert parse_approval_command("/deny") == "deny"

    def test_deny_whitespace_only_returns_literal(self) -> None:
        assert parse_approval_command("/deny   ") == "deny"

    def test_deny_reason_chinese(self) -> None:
        result = parse_approval_command("/deny 请用更安全的方法")
        assert isinstance(result, DenyWithReason)
        assert result.reason == "请用更安全的方法"

    def test_deny_reason_truncation(self) -> None:
        long_reason = "x" * 500
        result = parse_approval_command(f"/deny {long_reason}")
        assert isinstance(result, DenyWithReason)
        assert len(result.reason) == 280

    def test_deny_reason_preserves_original_case(self) -> None:
        result = parse_approval_command("/Deny Use HTTPS instead")
        assert isinstance(result, DenyWithReason)
        assert result.reason == "Use HTTPS instead"


class TestParseTopicArgs:
    def test_bind_no_agent(self) -> None:
        result = parse_topic_args("bind", "")
        assert result.action == "bind"
        assert result.agent_id is None

    def test_bind_with_agent(self) -> None:
        result = parse_topic_args("bind", "my-agent")
        assert result == TopicCommand(action="bind", agent_id="my-agent")

    def test_bind_with_workspace_project(self) -> None:
        result = parse_topic_args("bind", "agent=writer workspace=project:abc-123")
        assert result.agent_id == "writer"
        assert result.project_id == "abc-123"

    def test_bind_with_workspace_path(self) -> None:
        result = parse_topic_args("bind", "workspace=/tmp/vault")
        assert result.authorized_path == "/tmp/vault"

    def test_unbind(self) -> None:
        result = parse_topic_args("unbind", "")
        assert result.action == "unbind"

    def test_topic(self) -> None:
        result = parse_topic_args("topic", "")
        assert result.action == "topic"


class TestAgentRouteCommands:
    def test_known_alias_with_message(self) -> None:
        registry = _make_registry()
        resolved = registry.resolve("/cc hello world")
        assert resolved is not None
        assert resolved.command_def.kind == CommandKind.AGENT_ROUTE
        assert resolved.command_def.agent_id == "claude"
        assert resolved.raw_args == "hello world"

    def test_known_alias_no_message(self) -> None:
        registry = _make_registry()
        resolved = registry.resolve("/gm")
        assert resolved is not None
        assert resolved.command_def.agent_id == "gemini"
        assert resolved.raw_args == ""

    def test_unknown_alias(self) -> None:
        registry = _make_registry()
        assert registry.resolve("/xx test") is None

    def test_all_aliases(self) -> None:
        registry = _make_registry()
        aliases = {
            "cc": "claude",
            "cx": "codex",
            "cs": "cursor",
            "km": "kimi",
            "gm": "gemini",
            "oc": "openclaw",
            "ocd": "opencode",
        }
        for alias, expected_id in aliases.items():
            resolved = registry.resolve(f"/{alias} test")
            assert resolved is not None, f"/{alias} should be recognized"
            assert resolved.command_def.agent_id == expected_id
