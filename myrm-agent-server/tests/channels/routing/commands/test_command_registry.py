"""``CommandRegistry``: validation, edge cases, unregistering and filtering."""

from __future__ import annotations

import pytest

from app.channels.routing.command_defs import (
    CommandDef,
    CommandKind,
)
from app.channels.routing.command_registry import (
    CommandRegistry,
)


class TestRegistryValidation:
    """Tests for CommandRegistry.register() validation and conflict detection."""

    def test_empty_name_raises(self) -> None:
        registry = CommandRegistry()
        with pytest.raises(ValueError, match="non-empty"):
            registry.register(CommandDef(name="", description="bad"))

    def test_name_with_spaces_raises(self) -> None:
        registry = CommandRegistry()
        with pytest.raises(ValueError, match="without spaces"):
            registry.register(CommandDef(name="my cmd", description="bad"))

    def test_name_with_leading_slash_raises(self) -> None:
        registry = CommandRegistry()
        with pytest.raises(ValueError, match="leading '/'"):
            registry.register(CommandDef(name="/test", description="bad"))

    def test_overwrite_system_command_raises(self) -> None:
        registry = CommandRegistry()
        with pytest.raises(ValueError, match="Cannot overwrite system command"):
            registry.register(
                CommandDef(
                    name="stop",
                    description="hijack",
                    kind=CommandKind.SKILL,
                    skill_ids=("x",),
                )
            )

    def test_overwrite_system_alias_raises(self) -> None:
        registry = CommandRegistry()
        system_cmd = CommandDef(name="mycmd", description="safe", kind=CommandKind.SYSTEM, aliases=("mc",))
        registry.register(system_cmd)
        with pytest.raises(ValueError, match="Cannot overwrite system command alias"):
            registry.register(
                CommandDef(
                    name="another",
                    description="hijack",
                    aliases=("mc",),
                    kind=CommandKind.AGENT_ROUTE,
                )
            )

    def test_overwrite_non_system_logs_warning(self) -> None:
        registry = CommandRegistry()
        cmd1 = CommandDef(
            name="custom",
            description="first",
            kind=CommandKind.AGENT_ROUTE,
            agent_id="a1",
        )
        cmd2 = CommandDef(
            name="custom",
            description="second",
            kind=CommandKind.AGENT_ROUTE,
            agent_id="a2",
        )
        registry.register(cmd1)
        registry.register(cmd2)
        resolved = registry.resolve("/custom")
        assert resolved is not None
        assert resolved.command_def.agent_id == "a2"

    def test_overwrite_cleans_old_aliases(self) -> None:
        """Overwriting a non-SYSTEM command must remove the old aliases from _lookup."""
        registry = CommandRegistry()
        cmd1 = CommandDef(
            name="report",
            description="first",
            kind=CommandKind.SKILL,
            skill_ids=("s1",),
            aliases=("rpt", "rp"),
        )
        cmd2 = CommandDef(
            name="report",
            description="second",
            kind=CommandKind.SKILL,
            skill_ids=("s2",),
            aliases=("rep",),
        )
        registry.register(cmd1)
        assert registry.get("rpt") is cmd1
        assert registry.get("rp") is cmd1

        registry.register(cmd2)
        assert registry.get("report") is cmd2
        assert registry.get("rep") is cmd2
        assert registry.get("rpt") is None, "old alias 'rpt' should be cleaned up"
        assert registry.get("rp") is None, "old alias 'rp' should be cleaned up"

    def test_invalid_alias_raises(self) -> None:
        registry = CommandRegistry()
        with pytest.raises(ValueError, match="Invalid alias"):
            registry.register(CommandDef(name="good", description="ok", aliases=("bad alias",)))


class TestRegistryEdgeCases:
    """Tests for edge cases in CommandRegistry.resolve()."""

    def test_resolve_bare_slash_returns_none(self) -> None:
        registry = CommandRegistry()
        assert registry.resolve("/") is None

    def test_resolve_args_on_non_parse_args_cmd_returns_none(self) -> None:
        registry = CommandRegistry()
        resolved = registry.resolve("/stop extra args")
        assert resolved is None

    def test_all_commands_returns_list(self) -> None:
        registry = CommandRegistry()
        cmds = registry.all_commands()
        assert isinstance(cmds, list)
        assert len(cmds) > 0

    def test_help_lines_with_alias(self) -> None:
        registry = CommandRegistry()
        cmd = CommandDef(
            name="report",
            description="Generate report",
            kind=CommandKind.SKILL,
            skill_ids=("s1",),
            aliases=("rpt",),
            category="Skill",
        )
        registry.register(cmd)
        text = "\n".join(registry.help_lines())
        assert "alias" in text
        assert "/rpt" in text


class TestRegistryUnregisterAndFilter:
    """Tests for CommandRegistry.unregister() and commands_by_kind()."""

    def test_unregister_existing(self) -> None:
        registry = CommandRegistry()
        cmd = CommandDef(name="custom", description="test", kind=CommandKind.SKILL, skill_ids=("s1",))
        registry.register(cmd)
        assert registry.unregister("custom") is True
        assert registry.get("custom") is None

    def test_unregister_removes_aliases(self) -> None:
        registry = CommandRegistry()
        cmd = CommandDef(
            name="report",
            description="test",
            kind=CommandKind.SKILL,
            skill_ids=("s1",),
            aliases=("rpt", "rp"),
        )
        registry.register(cmd)
        registry.unregister("report")
        assert registry.get("rpt") is None
        assert registry.get("rp") is None

    def test_unregister_nonexistent(self) -> None:
        registry = CommandRegistry()
        assert registry.unregister("nonexistent") is False

    def test_commands_by_kind_system(self) -> None:
        registry = CommandRegistry()
        system_cmds = registry.commands_by_kind(CommandKind.SYSTEM)
        assert len(system_cmds) > 0
        assert all(c.kind == CommandKind.SYSTEM for c in system_cmds)

    def test_commands_by_kind_skill(self) -> None:
        registry = CommandRegistry()
        base_count = len(registry.commands_by_kind(CommandKind.SKILL))
        cmd = CommandDef(name="daily", description="test", kind=CommandKind.SKILL, skill_ids=("s1",))
        registry.register(cmd)
        skill_cmds = registry.commands_by_kind(CommandKind.SKILL)
        assert len(skill_cmds) == base_count + 1
        assert any(c.name == "daily" for c in skill_cmds)

    def test_commands_by_kind_empty(self) -> None:
        registry = CommandRegistry()
        assert len(registry.commands_by_kind(CommandKind.AGENT_ROUTE)) == 0

    def test_help_lines_includes_skill_commands(self) -> None:
        registry = CommandRegistry()
        cmd = CommandDef(
            name="daily",
            description="Daily report",
            kind=CommandKind.SKILL,
            skill_ids=("s1",),
            category="Skill",
        )
        registry.register(cmd)
        lines = registry.help_lines()
        help_text = "\n".join(lines)
        assert "/daily" in help_text
        assert "Daily report" in help_text
