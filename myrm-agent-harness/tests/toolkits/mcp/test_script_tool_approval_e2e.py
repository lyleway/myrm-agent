"""A script's call to an MCP tool reaches the record the connection pool filed for it, over real stdio servers.

Every link is the production one: the stdio server and the connection pool that files a record for each tool it
lists, the skill generator that names and numbers the skills, the skill registry a script's import resolves through,
the approval stage that reads the record into the card, and the proxy that runs the call. Each of them was tested
against a hand-built stand-in of its neighbour, and the stand-ins agreed on a key the real chain never produced.

[INPUT]
- tests.support.mcp_testkit.annotations_probe_server (POS: a real server declaring every kind of hint, run as a stdio child)
- myrm_agent_harness.agent.skills.mcp.core_generator::mcp_skill_generator (POS: names the skills of a pool's servers)
- myrm_agent_harness.agent.middlewares.approval.batch_processor::evaluate_tool_batch (POS: approval verdicts and cards)

[OUTPUT]
- Tests of the registry key a script's call is looked up by, of the tool approval names against the tool the proxy
  runs, and of evicting one server's records.

[POS]
Guards the key space of the tool safety registry end to end, so a change that files records under a spelling the
script path does not look up, or evicts under one the pool did not file, fails here and not in production.
"""

from __future__ import annotations

import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from langchain_core.messages import ToolCall

from myrm_agent_harness.agent.middlewares.approval.batch_processor import evaluate_tool_batch
from myrm_agent_harness.agent.security.ptc_verifier import extract_ptc_intent
from myrm_agent_harness.agent.security.types import PathPolicy, SecurityConfig
from myrm_agent_harness.agent.skills.mcp.core_generator import mcp_skill_generator
from myrm_agent_harness.agent.skills.mcp.executor import SkillExecutor
from myrm_agent_harness.agent.skills.mcp.proxy_service import MCPSkillProxyService
from myrm_agent_harness.agent.skills.mcp.script_tool import resolve_script_tool
from myrm_agent_harness.agent.skills.runtime.registry import skill_registry
from myrm_agent_harness.backends.skills.types import SkillMetadata
from myrm_agent_harness.core.security.tool_registry import (
    MCPAnnotations,
    SafetyMetadata,
    register_ptc_safety_metadata,
)
from myrm_agent_harness.core.security.tool_registry.registry import _PTC_LOCK, _PTC_TOOL_SAFETY
from myrm_agent_harness.toolkits.mcp.config import MCPConfig, mcp_tool_prefix
from myrm_agent_harness.toolkits.mcp.connection_manager import MCPConnectionManager
from myrm_agent_harness.toolkits.mcp.tool_processing import evict_tool_annotations
from tests.support.mcp_testkit import annotations_probe_server

_SCRIPT = Path(annotations_probe_server.__file__)

# Two servers that fold to the same skill name: the generator numbers the second, and the case-sensitive prefixes
# keep their tool names apart.
_SERVERS = ("annotations-probe", "Annotations_Probe")

# What the card must carry for each tool, from what the probe server declares about it.
_CLAIMS: dict[str, MCPAnnotations] = {
    "delete_it": {"destructiveHint": True},
    "fetch_web": {"openWorldHint": True},
    "read_only": {},
    "plain": {},
}


@dataclass(frozen=True, slots=True)
class Served:
    """The skills the generator made for the servers, and the records the pool filed while connecting."""

    skills: list[SkillMetadata]
    filed: dict[str, tuple[SafetyMetadata, MCPAnnotations]]


@pytest_asyncio.fixture(scope="module")
async def _served_once(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[Served]:
    manager = MCPConnectionManager()
    await manager.start()
    configs = [
        MCPConfig(
            type="stdio",
            command=sys.executable,
            args=[str(_SCRIPT), str(tmp_path_factory.mktemp(f"marks{index}"))],
            name=name,
            description="annotations probe",
            connect_timeout=30.0,
        )
        for index, name in enumerate(_SERVERS)
    ]
    try:
        with patch(
            "myrm_agent_harness.toolkits.mcp.connection_manager.get_mcp_connection_manager",
            AsyncMock(return_value=manager),
        ):
            skills = await mcp_skill_generator.generate_metadata_only(configs)
            with _PTC_LOCK:
                filed = dict(_PTC_TOOL_SAFETY)
            yield Served(skills=skills, filed=filed)
    finally:
        await manager.stop()


@pytest.fixture
def served(_served_once: Served) -> Iterator[Served]:
    """The skills and records as the pool left them (``tests/conftest.py`` empties the registry between tests)."""
    for name, (record, claims) in _served_once.filed.items():
        register_ptc_safety_metadata(name, record, claims)
    for skill in _served_once.skills:
        skill_registry.register(skill)
    yield _served_once
    skill_registry.clear()


async def _card_context(skill: str, tool: str) -> dict[str, object]:
    """Approve a script that imports and calls ``tool`` of ``skill``; the call must be left pending, with its card."""
    approved, denied, pending = await evaluate_tool_batch(
        tool_calls=[
            ToolCall(
                name="bash_code_execute_tool",
                args={"command": f'python -c "from skills.{skill} import _{tool}; _{tool}()"'},
                id="call_script",
            )
        ],
        config=SecurityConfig(auto_mode_enabled=False, path_policy=PathPolicy()),
        is_cron=False,
        workspace_root="/workspace",
        session_key="script-tool-e2e",
        args_hashes={},
    )
    assert approved == []
    assert denied == []
    assert len(pending) == 1
    return pending[0][4] or {}


def test_the_pool_files_each_tool_under_its_prefixed_name_and_nothing_else(served: Served) -> None:
    assert [skill.name for skill in served.skills] == ["mcp_annotations_probe_skill", "mcp_annotations_probe_2_skill"]

    expected = {tool for skill in served.skills for tool in (skill.mcp.tools if skill.mcp else [])}

    assert set(served.filed) == expected
    for skill in served.skills:
        assert skill.mcp is not None
        assert all(tool.startswith(mcp_tool_prefix(skill.mcp.server)) for tool in skill.mcp.tools)


def test_every_generated_skill_is_recognized_as_a_script_skill(served: Served) -> None:
    """Script execution, approval and the generator agree on what a skill's name looks like, numbered or not."""
    for skill in served.skills:
        command = f'python -c "from skills.{skill.name} import _read_only; _read_only()"'

        assert SkillExecutor().detect_skill_in_command(command) == (True, skill.name)
        intent = extract_ptc_intent(command)
        assert intent is not None
        assert intent[:2] == (skill.name, "read_only")


@pytest.mark.parametrize("server_index", [0, 1], ids=["first-skill", "skill-renamed-for-a-collision"])
@pytest.mark.parametrize("tool", sorted(_CLAIMS))
async def test_a_script_call_reaches_the_claim_the_server_declared(
    served: Served, server_index: int, tool: str
) -> None:
    context = await _card_context(served.skills[server_index].name, tool)

    assert context["ptc_annotations"] == _CLAIMS[tool]


async def test_approval_names_the_tool_the_proxy_runs(served: Served) -> None:
    service = MCPSkillProxyService()

    for skill in served.skills:
        assert skill.mcp is not None
        assert resolve_script_tool(skill.name, "delete_it") == f"{mcp_tool_prefix(skill.mcp.server)}delete_it"
        assert "deleted" in str(await service.invoke_tool(skill.name, "delete_it", {}, session_id="script-tool-e2e"))


async def test_a_call_to_a_tool_the_server_does_not_have_reaches_no_record(served: Served) -> None:
    assert resolve_script_tool(served.skills[0].name, "not_a_tool") is None
    assert resolve_script_tool("mcp_not_served_skill", "delete_it") is None

    assert "ptc_annotations" not in await _card_context(served.skills[0].name, "not_a_tool")


async def test_evicting_one_server_drops_its_records_and_leaves_the_other_servers(served: Served) -> None:
    first, second = served.skills
    assert first.mcp is not None
    assert second.mcp is not None

    assert evict_tool_annotations(first.mcp.server) == len(first.mcp.tools)

    assert all(name not in _PTC_TOOL_SAFETY for name in first.mcp.tools)
    assert all(name in _PTC_TOOL_SAFETY for name in second.mcp.tools)
    assert "ptc_annotations" not in await _card_context(first.name, "delete_it")
    assert (await _card_context(second.name, "delete_it"))["ptc_annotations"] == _CLAIMS["delete_it"]
