"""``PERSISTENT_STATE_MUTATOR_TOOLS``: the one list a read-only session and the untrusted ingress fence share."""

from __future__ import annotations

from collections.abc import Sequence
from unittest.mock import MagicMock

from myrm_agent_harness.agent.security.config import read_only_permissions
from myrm_agent_harness.agent.security.guards.untrusted_ingress_fence import (
    DISALLOWED_UNTRUSTED_TOOLS,
    is_tool_allowed_under_untrusted_ingress,
)
from myrm_agent_harness.core.security.tool_registry import (
    BUILTIN_TOOL_NAMES,
    PERSISTENT_STATE_MUTATOR_TOOLS,
    resolve_permission_type,
)
from myrm_agent_harness.toolkits.kanban._orchestrator_tools import build_orchestrator_tools
from myrm_agent_harness.toolkits.kanban._worker_tools import build_worker_tools

# The orchestrator tool that only reads the board.
_KANBAN_READ_ONLY_ORCHESTRATOR_TOOLS = frozenset({"kanban_list_tasks"})


def _kanban_names(tools: Sequence[object]) -> frozenset[str]:
    return frozenset(str(getattr(tool, "name", "")) for tool in tools)


def test_every_entry_is_a_built_in_tool_governed_under_its_own_name() -> None:
    assert PERSISTENT_STATE_MUTATOR_TOOLS <= BUILTIN_TOOL_NAMES
    for name in PERSISTENT_STATE_MUTATOR_TOOLS:
        assert resolve_permission_type(name) == name


def test_the_list_holds_every_kanban_orchestrator_tool_that_changes_the_board() -> None:
    orchestrator = _kanban_names(build_orchestrator_tools(MagicMock(), None))
    kanban_mutators = {name for name in PERSISTENT_STATE_MUTATOR_TOOLS if name.startswith("kanban_")}
    assert orchestrator - _KANBAN_READ_ONLY_ORCHESTRATOR_TOOLS == kanban_mutators


def test_the_list_holds_no_kanban_worker_tool() -> None:
    workers = _kanban_names(build_worker_tools(MagicMock(), None))
    assert not workers & PERSISTENT_STATE_MUTATOR_TOOLS


def test_a_read_only_session_denies_every_entry() -> None:
    denied = {name for name, action in read_only_permissions().items() if action == "deny"}
    assert denied >= PERSISTENT_STATE_MUTATOR_TOOLS


def test_the_untrusted_ingress_fence_fences_every_entry() -> None:
    assert PERSISTENT_STATE_MUTATOR_TOOLS <= DISALLOWED_UNTRUSTED_TOOLS
    for name in PERSISTENT_STATE_MUTATOR_TOOLS:
        assert not is_tool_allowed_under_untrusted_ingress(name)
