"""Skill-bound commands in ``CommandRegistry``: registration, updates and the productivity skills."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.channels.routing.command_defs import (
    CommandDef,
    CommandKind,
)
from app.channels.routing.command_registry import (
    CommandRegistry,
)

if TYPE_CHECKING:
    from app.channels.core.gateway import ChannelGateway


class TestSkillCommandRegistration:
    """Tests for SKILL command registration, resolution, and robustness."""

    def test_skill_command_resolve(self) -> None:
        registry = CommandRegistry()
        cmd = CommandDef(
            name="daily-report",
            description="Generate daily report",
            kind=CommandKind.SKILL,
            skill_ids=("daily_report_skill",),
            aliases=("dr",),
            parse_args=True,
            category="Skill",
        )
        registry.register(cmd)
        resolved = registry.resolve("/daily-report some args")
        assert resolved is not None
        assert resolved.command_def.kind == CommandKind.SKILL
        assert resolved.command_def.skill_ids == ("daily_report_skill",)
        assert resolved.raw_args == "some args"

    def test_skill_command_resolve_via_alias(self) -> None:
        registry = CommandRegistry()
        cmd = CommandDef(
            name="daily-report",
            description="test",
            kind=CommandKind.SKILL,
            skill_ids=("s1",),
            aliases=("dr",),
            parse_args=True,
        )
        registry.register(cmd)
        resolved = registry.resolve("/dr hello")
        assert resolved is not None
        assert resolved.command_def.name == "daily-report"
        assert resolved.raw_args == "hello"

    def test_skill_command_no_args(self) -> None:
        registry = CommandRegistry()
        cmd = CommandDef(
            name="daily-report",
            description="test",
            kind=CommandKind.SKILL,
            skill_ids=("s1",),
            parse_args=True,
        )
        registry.register(cmd)
        resolved = registry.resolve("/daily-report")
        assert resolved is not None
        assert resolved.raw_args == ""

    def test_register_skill_with_system_name_raises(self) -> None:
        registry = CommandRegistry()
        with pytest.raises(ValueError, match="Cannot overwrite system command"):
            registry.register(
                CommandDef(
                    name="stop",
                    description="bad",
                    kind=CommandKind.SKILL,
                    skill_ids=("x",),
                )
            )


class TestUpdateSkillCommands:
    """Tests for ChannelGateway.update_skill_commands() runtime hot-reload."""

    def _make_gateway_with_registry(self) -> tuple[ChannelGateway, CommandRegistry]:
        """Create a minimal gateway with router containing a CommandRegistry."""
        from app.channels.core.gateway import ChannelGateway

        gw = ChannelGateway()
        mock_router = MagicMock()
        mock_router._registry = CommandRegistry()
        gw._router = mock_router
        return gw, mock_router._registry

    def test_add_skill_commands(self) -> None:
        gw, registry = self._make_gateway_with_registry()
        cmds = (
            CommandDef(
                name="report",
                description="test",
                kind=CommandKind.SKILL,
                skill_ids=("s1",),
            ),
            CommandDef(
                name="analyze",
                description="test",
                kind=CommandKind.SKILL,
                skill_ids=("s2",),
            ),
        )
        gw.update_skill_commands(cmds)
        assert len(registry.commands_by_kind(CommandKind.SKILL)) == 2
        assert registry.get("report") is not None
        assert registry.get("analyze") is not None

    def test_replace_skill_commands(self) -> None:
        gw, registry = self._make_gateway_with_registry()
        old_cmds = (
            CommandDef(
                name="old-cmd",
                description="old",
                kind=CommandKind.SKILL,
                skill_ids=("s1",),
            ),
        )
        gw.update_skill_commands(old_cmds)
        assert registry.get("old-cmd") is not None

        new_cmds = (
            CommandDef(
                name="new-cmd",
                description="new",
                kind=CommandKind.SKILL,
                skill_ids=("s2",),
            ),
        )
        gw.update_skill_commands(new_cmds)
        assert registry.get("old-cmd") is None
        assert registry.get("new-cmd") is not None
        assert len(registry.commands_by_kind(CommandKind.SKILL)) == 1

    def test_clear_all_skill_commands(self) -> None:
        gw, registry = self._make_gateway_with_registry()
        cmds = (
            CommandDef(
                name="cmd1",
                description="test",
                kind=CommandKind.SKILL,
                skill_ids=("s1",),
            ),
        )
        gw.update_skill_commands(cmds)
        assert len(registry.commands_by_kind(CommandKind.SKILL)) == 1

        gw.update_skill_commands(())
        assert len(registry.commands_by_kind(CommandKind.SKILL)) == 0

    def test_system_commands_preserved(self) -> None:
        gw, registry = self._make_gateway_with_registry()
        system_count_before = len(registry.commands_by_kind(CommandKind.SYSTEM))
        cmds = (
            CommandDef(
                name="report",
                description="test",
                kind=CommandKind.SKILL,
                skill_ids=("s1",),
            ),
        )
        gw.update_skill_commands(cmds)
        system_count_after = len(registry.commands_by_kind(CommandKind.SYSTEM))
        assert system_count_before == system_count_after

    def test_invalid_command_skipped_others_registered(self) -> None:
        """One invalid command (system name conflict) should not prevent others."""
        gw, registry = self._make_gateway_with_registry()
        cmds = (
            CommandDef(
                name="valid-cmd",
                description="ok",
                kind=CommandKind.SKILL,
                skill_ids=("s1",),
            ),
            CommandDef(
                name="stop",
                description="bad",
                kind=CommandKind.SKILL,
                skill_ids=("s2",),
            ),
            CommandDef(
                name="another-valid",
                description="ok",
                kind=CommandKind.SKILL,
                skill_ids=("s3",),
            ),
        )
        gw.update_skill_commands(cmds)
        assert registry.get("valid-cmd") is not None
        assert registry.get("another-valid") is not None
        resolved = registry.resolve("/stop")
        assert resolved is not None
        assert resolved.command_def.kind == CommandKind.SYSTEM

    def test_no_router_is_noop(self) -> None:
        from app.channels.core.gateway import ChannelGateway

        gw = ChannelGateway()
        cmds = (
            CommandDef(
                name="cmd",
                description="test",
                kind=CommandKind.SKILL,
                skill_ids=("s1",),
            ),
        )
        gw.update_skill_commands(cmds)  # should not raise


class TestProductivitySkillCommands:
    """Verifies that /memo and /review-week commands resolve correctly in the CommandRegistry."""

    def test_memo_command_resolution(self) -> None:
        registry = CommandRegistry()
        resolved = registry.resolve("/memo 整理今天上午的架构评审录音")
        assert resolved is not None
        assert resolved.command_def.name == "memo"
        assert resolved.command_def.kind == CommandKind.SKILL
        assert "voice-memo-synthesizer" in resolved.command_def.skill_ids
        assert resolved.raw_args == "整理今天上午的架构评审录音"

        # Aliases
        resolved_meeting = registry.resolve("/meeting 项目排期讨论")
        assert resolved_meeting is not None
        assert resolved_meeting.command_def.name == "memo"

        resolved_minutes = registry.resolve("/minutes")
        assert resolved_minutes is not None
        assert resolved_minutes.command_def.name == "memo"

    def test_review_week_command_resolution(self) -> None:
        registry = CommandRegistry()
        resolved = registry.resolve("/review-week 7d")
        assert resolved is not None
        assert resolved.command_def.name == "review-week"
        assert resolved.command_def.kind == CommandKind.SKILL
        assert "voice-corpus-review" in resolved.command_def.skill_ids
        assert resolved.raw_args == "7d"

        # Aliases
        resolved_week = registry.resolve("/week-review")
        assert resolved_week is not None
        assert resolved_week.command_def.name == "review-week"

        resolved_blockers = registry.resolve("/extract-blockers 看板阻碍分析")
        assert resolved_blockers is not None
        assert resolved_blockers.command_def.name == "review-week"
        assert resolved_blockers.raw_args == "看板阻碍分析"
