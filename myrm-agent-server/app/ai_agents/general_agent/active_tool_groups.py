"""Derive harness canonical tool groups from GeneralAgent enable flags.

[INPUT]
- GeneralAgent boolean flags and media params (POS: product-layer entitlement)

[OUTPUT]
- derive_active_tool_groups: list of TOOL_GROUP_MAP keys the agent has enabled (skill visibility + AgentRuntimeSpec.tool_groups)
- ACTIVE_TOOL_GROUP_KEYS: stable key tuple for architecture tests
- Catalog parity: test_active_tool_groups asserts BUILTIN_TOOL_ID_TO_GROUP keys match TOGGLABLE minus skill_market/skill_manage (those toggles have no tool group).

[POS]
Server SSOT mapping user entitlement → harness ``active_tool_groups`` passed to
``sync_discover_capability_tool`` and ``AgentRuntimeSpec.tool_groups``.
"""

from __future__ import annotations

from typing import Protocol

from myrm_agent_harness.agent.meta_tools.mount_policy import FileAccessMode
from myrm_agent_harness.toolkits.memory.config import RecallMode


class ActiveToolGroupSource(Protocol):
    enable_web_search: bool
    enable_browser: bool
    file_access_mode: FileAccessMode
    enable_shell_tools: bool
    enable_computer_use: bool
    enable_memory: bool
    memory_recall_mode: RecallMode
    incognito_mode: bool
    enable_kanban: bool
    enable_wiki: bool
    enable_structured_clarify: bool
    enable_external_cli: bool
    enable_cron_eager: bool

    # Read-only so that a source may declare a narrower concrete params type.
    @property
    def image_generation_params(self) -> object | None: ...

    @property
    def video_generation_params(self) -> object | None: ...

    @property
    def tts_params(self) -> object | None: ...


ACTIVE_TOOL_GROUP_KEYS: tuple[str, ...] = (
    "web",
    "browser",
    "file_ops",
    "shell",
    "computer_use",
    "memory",
    "kanban",
    "wiki",
    "planning",
    "structured_clarify",
    "cron",
    "image_generation",
    "video_generation",
    "tts",
    "external_cli",
)


def derive_active_tool_groups(
    agent: ActiveToolGroupSource,
    *,
    enable_planning: bool,
) -> list[str]:
    """Map GeneralAgent flags to harness TOOL_GROUP_MAP group names."""
    flag_to_group: list[tuple[str, bool]] = [
        ("web", agent.enable_web_search),
        ("browser", agent.enable_browser),
        ("file_ops", agent.file_access_mode == FileAccessMode.FULL),
        ("shell", agent.enable_shell_tools),
        ("computer_use", agent.enable_computer_use),
        ("memory", agent.enable_memory and not agent.incognito_mode and agent.memory_recall_mode != RecallMode.CONTEXT),
        ("kanban", agent.enable_kanban),
        ("wiki", agent.enable_wiki),
        ("planning", enable_planning),
        ("structured_clarify", agent.enable_structured_clarify),
        ("cron", agent.enable_cron_eager),
        ("image_generation", agent.image_generation_params is not None),
        ("video_generation", agent.video_generation_params is not None),
        ("tts", agent.tts_params is not None),
        ("external_cli", agent.enable_external_cli),
    ]
    return [group for group, enabled in flag_to_group if enabled]
