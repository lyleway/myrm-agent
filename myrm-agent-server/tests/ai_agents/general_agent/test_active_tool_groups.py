"""Unit tests for the active_tool_groups SSOT."""

from __future__ import annotations

from types import SimpleNamespace

from myrm_agent_harness.agent.meta_tools.mount_policy import FileAccessMode
from myrm_agent_harness.agent.tool_management.tool_catalog import BUILTIN_TOOL_ID_TO_GROUP
from myrm_agent_harness.toolkits.memory.config import RecallMode

from app.ai_agents.general_agent.active_tool_groups import (
    ACTIVE_TOOL_GROUP_KEYS,
    derive_active_tool_groups,
)


def _agent(**overrides: object) -> SimpleNamespace:
    base = dict(
        enable_web_search=True,
        enable_browser=False,
        file_access_mode=FileAccessMode.FULL,
        enable_shell_tools=True,
        enable_computer_use=False,
        enable_memory=True,
        memory_recall_mode=RecallMode.HYBRID,
        incognito_mode=False,
        enable_kanban=False,
        enable_wiki=False,
        enable_answer_tool=False,
        enable_structured_clarify=False,
        enable_external_cli=False,
        enable_cron_eager=False,
        image_generation_params=None,
        video_generation_params=None,
        tts_params=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_active_tool_group_keys_match_derive_tuple_length() -> None:
    agent = _agent(
        enable_browser=True,
        enable_computer_use=True,
        enable_kanban=True,
        enable_wiki=True,
        enable_answer_tool=True,
        enable_structured_clarify=True,
        enable_external_cli=True,
        enable_cron_eager=True,
        image_generation_params=object(),
        video_generation_params=object(),
        tts_params=object(),
    )
    groups = derive_active_tool_groups(agent, enable_planning=True)
    assert len(groups) == len(ACTIVE_TOOL_GROUP_KEYS)
    assert set(groups) == set(ACTIVE_TOOL_GROUP_KEYS)


def test_derive_includes_external_cli_when_enabled() -> None:
    groups = derive_active_tool_groups(
        _agent(enable_external_cli=True),
        enable_planning=False,
    )
    assert "external_cli" in groups


def test_derive_includes_cron_when_eager_enabled() -> None:
    groups = derive_active_tool_groups(_agent(enable_cron_eager=True), enable_planning=False)
    assert "cron" in groups


def test_derive_excludes_cron_when_eager_disabled() -> None:
    groups = derive_active_tool_groups(_agent(enable_cron_eager=False), enable_planning=False)
    assert "cron" not in groups


def test_derive_media_groups_from_params_presence() -> None:
    groups = derive_active_tool_groups(
        _agent(
            image_generation_params={"model": "dall-e"},
            video_generation_params={"provider": "openai"},
            tts_params={"model": "tts-1"},
        ),
        enable_planning=False,
    )
    assert "image_generation" in groups
    assert "video_generation" in groups
    assert "tts" in groups


def test_incognito_excludes_memory() -> None:
    groups = derive_active_tool_groups(_agent(incognito_mode=True), enable_planning=False)
    assert "memory" not in groups


def test_memory_off_excludes_memory_group() -> None:
    groups = derive_active_tool_groups(_agent(enable_memory=False), enable_planning=False)
    assert "memory" not in groups


def test_memory_group_follows_whether_memory_tools_are_mounted() -> None:
    for recall_mode, mounted in [
        (RecallMode.HYBRID, True),
        (RecallMode.TOOLS, True),
        (RecallMode.CONTEXT, False),
    ]:
        groups = derive_active_tool_groups(_agent(memory_recall_mode=recall_mode), enable_planning=False)
        assert ("memory" in groups) is mounted, recall_mode


def test_builtin_tool_id_to_group_values_subset_of_active_keys() -> None:
    assert set(BUILTIN_TOOL_ID_TO_GROUP.values()).issubset(set(ACTIVE_TOOL_GROUP_KEYS))


def test_builtin_tool_id_to_group_keys_match_togglable_catalog() -> None:
    from app.services.agent.builtin_specs.builtin_tool_ids import TOGGLABLE_BUILTIN_TOOL_IDS

    without_tool_group = frozenset({"skill_market", "skill_manage"})

    assert set(BUILTIN_TOOL_ID_TO_GROUP) == set(TOGGLABLE_BUILTIN_TOOL_IDS) - without_tool_group
    assert without_tool_group <= set(TOGGLABLE_BUILTIN_TOOL_IDS)
