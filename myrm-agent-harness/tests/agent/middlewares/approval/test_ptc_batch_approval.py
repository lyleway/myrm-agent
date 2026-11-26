"""PTC approval in the batch processor: a PTC call to an MCP tool always asks, whatever record the registry holds,
and the server's cautionary claims reach the approval card.

The skill registry, the tool registry and the script parser are the production ones: a script reaches the record of
the tool it calls through the same name resolution the proxy runs the call with, whatever the server is named.
"""

import pytest
from langchain_core.messages import ToolCall

from myrm_agent_harness.agent.middlewares.approval.batch_processor import evaluate_tool_batch
from myrm_agent_harness.agent.security.tool_registry import SafetyMetadata
from myrm_agent_harness.agent.security.types import PathPolicy, SecurityConfig
from tests.agent.middlewares.approval.ptc_script_tool import connected_script_tool


async def _ask(command: str) -> tuple[list, list, list]:
    return await evaluate_tool_batch(
        tool_calls=[ToolCall(name="bash_code_execute_tool", args={"command": command}, id="call_ptc")],
        config=SecurityConfig(auto_mode_enabled=False, path_policy=PathPolicy()),
        is_cron=False,
        workspace_root="/workspace",
        session_key="test_sess",
        args_hashes={},
    )


def _import_and_call(skill: str, function: str, args: str = "") -> str:
    return f'python -c "from skills.{skill} import _{function}; _{function}({args})"'


@pytest.mark.asyncio
async def test_evaluate_ptc_read_only_record_is_still_pending():
    """A record that looks read-only (no server claim can produce one) never auto-approves a PTC call."""
    record = SafetyMetadata(is_read_only=True, is_concurrent_safe=True, is_idempotent=True)

    with connected_script_tool("mcp_github_skill", "github", "read_file", record, {}):
        auto_approved, auto_denied, pending = await _ask(
            _import_and_call("mcp_github_skill", "read_file", "path='/workspace/repo'")
        )

    assert len(auto_approved) == 0
    assert len(auto_denied) == 0
    assert len(pending) == 1


@pytest.mark.asyncio
async def test_evaluate_ptc_destructive_is_pending():
    """A destructive PTC call remains in pending_approval, and the card carries the server's claim."""
    with connected_script_tool(
        "mcp_github_skill", "github", "write_file", SafetyMetadata(is_destructive=True), {"destructiveHint": True}
    ):
        auto_approved, auto_denied, pending = await _ask(
            _import_and_call("mcp_github_skill", "write_file", "path='/workspace/repo/file.txt'")
        )

    assert len(auto_approved) == 0
    assert len(auto_denied) == 0
    assert len(pending) == 1

    _idx, _tc, _perm_type, _reason, extra_ctx = pending[0]
    assert extra_ctx is not None
    assert extra_ctx["ptc_tool_name_full"] == "ptc:mcp_github_skill.write_file"
    assert extra_ctx["ptc_annotations"] == {"destructiveHint": True}


@pytest.mark.asyncio
async def test_evaluate_ptc_contradictory_annotations_is_pending():
    """A record that is both read-only and destructive stays pending in PTC path, and the card shows the caution."""
    record = SafetyMetadata(is_read_only=True, is_destructive=True, is_concurrent_safe=True)

    with connected_script_tool("mcp_buggy_skill", "buggy", "trap_tool", record, {"destructiveHint": True}):
        auto_approved, auto_denied, pending = await _ask(
            _import_and_call("mcp_buggy_skill", "trap_tool", "path='/workspace'")
        )

    assert len(auto_approved) == 0
    assert len(auto_denied) == 0
    assert len(pending) == 1
    assert pending[0][4]["ptc_annotations"] == {"destructiveHint": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skill", "server", "tool", "function"),
    [
        pytest.param("mcp_my_server_skill", "my-server", "delete_it", "delete_it", id="hyphenated-server"),
        pytest.param(
            "mcp_my_server_2_skill", "my_server", "delete_it", "delete_it", id="skill-renamed-for-a-collision"
        ),
        pytest.param("mcp_demo_skill", "demo", "delete-record", "delete_record", id="tool-name-with-a-hyphen"),
        pytest.param("mcp_demo_skill", "demo", "deleteRecord", "deleteRecord", id="camel-case-tool"),
    ],
)
async def test_the_claim_reaches_the_card_whatever_the_server_and_skill_are_named(
    skill: str, server: str, tool: str, function: str
):
    """The record is found under the tool's prefixed name, not under a spelling of the server or the function."""
    with connected_script_tool(skill, server, tool, SafetyMetadata(is_destructive=True), {"destructiveHint": True}):
        _approved, _denied, pending = await _ask(_import_and_call(skill, function))

    assert len(pending) == 1
    assert pending[0][4]["ptc_annotations"] == {"destructiveHint": True}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("skill", "function"),
    [
        pytest.param("mcp_unknown_skill", "delete_it", id="skill-not-served"),
        pytest.param("mcp_demo_skill", "not_a_tool", id="function-not-served"),
    ],
)
async def test_a_call_that_reaches_no_tool_still_asks_without_a_claim(skill: str, function: str):
    with connected_script_tool(
        "mcp_demo_skill", "demo", "delete_it", SafetyMetadata(is_destructive=True), {"destructiveHint": True}
    ):
        approved, _denied, pending = await _ask(_import_and_call(skill, function))

    assert approved == []
    assert len(pending) == 1
    assert "ptc_annotations" not in (pending[0][4] or {})
