"""The MCP servers of a turn reach Claude Code as ``--mcp-config`` files, and no other CLI is handed any.

The stand-in logs what every ``--mcp-config`` file held while the turn ran, so the files are read as the CLI reads them.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.mcp_servers import HttpMcpServer, McpServerConfig, StdioMcpServer
from myrm_agent_harness.toolkits.acp.runtime.cli.runtime import CliRuntime
from myrm_agent_harness.toolkits.acp.types import (
    MCP_NOT_ATTACHED_STATUS,
    PermissionBroker,
    PermissionRequest,
    PermissionVerdict,
    RuntimeEvent,
    RuntimeEventType,
)

from ._fakes import FakeCli, errors_of, make_runtime, text_of, types_of

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the CLI stand-ins are POSIX shebang scripts")

_MEMORY = HttpMcpServer(
    name="memory", url="http://127.0.0.1:9/mcp", headers={"Authorization": "Bearer t0ken"}, read_only=True
)
_LOCAL = StdioMcpServer(name="local", command="mcp-local", args=["--ro"], env={"KEY": "value"})
_BASH = {"tool_name": "Bash", "input": {"command": "ls", "description": "List files"}}


async def _turn(
    runtime: CliRuntime, servers: list[McpServerConfig], broker: PermissionBroker | None = None
) -> list[RuntimeEvent]:
    return [event async for event in runtime.run_turn("hello", "s1", mcp_servers=servers, permissions=broker)]


def _argv(fake_cli: FakeCli, name: str = "claude") -> list[str]:
    argv = fake_cli.calls(name)[0]["argv"]
    assert isinstance(argv, list)
    return argv


class _AllowAll:
    def __init__(self) -> None:
        self.asked: list[PermissionRequest] = []

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.asked.append(request)
        return PermissionVerdict(allowed=True)


class TestClaude:
    @pytest.mark.asyncio
    async def test_the_servers_are_in_a_config_file_in_the_form_claude_reads(self, fake_cli: FakeCli) -> None:
        events = await _turn(make_runtime(fake_cli), [_MEMORY, _LOCAL])

        assert errors_of(events) == []
        (config,) = fake_cli.mcp_configs()
        assert config == {
            "mcpServers": {
                "memory": {
                    "type": "http",
                    "url": "http://127.0.0.1:9/mcp",
                    "headers": {"Authorization": "Bearer t0ken"},
                },
                "local": {"command": "mcp-local", "args": ["--ro"], "env": {"KEY": "value"}},
            }
        }

    @pytest.mark.asyncio
    async def test_the_file_is_gone_when_the_turn_is_over_and_its_contents_never_reach_the_command_line(
        self, fake_cli: FakeCli
    ) -> None:
        await _turn(make_runtime(fake_cli), [_MEMORY])

        argv = _argv(fake_cli)
        path = Path(argv[argv.index("--mcp-config") + 1])
        assert not path.exists()
        assert "t0ken" not in " ".join(argv)

    @pytest.mark.asyncio
    async def test_the_tools_of_a_read_only_server_are_allowed_by_the_servers_name(self, fake_cli: FakeCli) -> None:
        await _turn(make_runtime(fake_cli), [_MEMORY, _LOCAL])

        argv = _argv(fake_cli)
        assert argv[argv.index("--allowedTools") + 1] == "mcp__memory"

    @pytest.mark.asyncio
    async def test_no_tool_is_allowed_in_advance_when_no_server_is_read_only(self, fake_cli: FakeCli) -> None:
        await _turn(make_runtime(fake_cli), [_LOCAL])

        assert "--allowedTools" not in _argv(fake_cli)

    @pytest.mark.asyncio
    async def test_the_host_is_still_asked_about_everything_else_next_to_the_servers(self, fake_cli: FakeCli) -> None:
        fake_cli.set_scenario({"asks": [_BASH]})
        broker = _AllowAll()

        events = await _turn(make_runtime(fake_cli), [_MEMORY], broker)

        assert errors_of(events) == []
        assert text_of(events) == "allow"
        assert [request.tool_name for request in broker.asked] == ["Bash"]
        assert [sorted(config["mcpServers"]) for config in fake_cli.mcp_configs()] == [["memory"], ["myrm_permissions"]]

    @pytest.mark.asyncio
    async def test_a_turn_without_servers_has_no_config_of_its_own(self, fake_cli: FakeCli) -> None:
        events = await _turn(make_runtime(fake_cli), [])

        assert "--mcp-config" not in _argv(fake_cli)
        assert RuntimeEventType.DONE in types_of(events)


class TestOtherClis:
    @pytest.mark.asyncio
    async def test_a_cli_that_is_not_claude_runs_without_the_servers_and_says_so(
        self, fake_cli: FakeCli, caplog: pytest.LogCaptureFixture
    ) -> None:
        runtime = make_runtime(fake_cli, name="my-agent")

        with caplog.at_level(logging.WARNING, logger="myrm_agent_harness.toolkits.acp.runtime.cli.runtime"):
            events = await _turn(runtime, [_MEMORY, _LOCAL])

        assert "--mcp-config" not in _argv(fake_cli, "my-agent")
        (notice,) = [e for e in events if e.data.get("status") == MCP_NOT_ATTACHED_STATUS]
        assert notice.data["servers"] == ["memory", "local"]
        assert any("cli_runtime_mcp_ignored" in record.message for record in caplog.records)
        assert RuntimeEventType.DONE in types_of(events)
