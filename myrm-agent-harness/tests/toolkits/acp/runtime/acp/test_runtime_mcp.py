"""MCP servers reach an ACP session in the form the agent takes: stdio always, HTTP only when the agent advertised it."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from acp.schema import HttpMcpServer as AcpHttpMcpServer
from acp.schema import McpServerStdio

from myrm_agent_harness.toolkits.acp.mcp_servers import HttpMcpServer, McpServerConfig, StdioMcpServer
from myrm_agent_harness.toolkits.acp.runtime.acp.capabilities import AgentCapabilities
from myrm_agent_harness.toolkits.acp.runtime.acp.sessions import AgentSessions, acp_mcp_servers
from myrm_agent_harness.toolkits.acp.runtime.mcp_status import MCP_NOT_ATTACHED_STATUS
from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    CliSessionRecord,
    PermissionBroker,
    PermissionRequest,
    PermissionVerdict,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
)

from ._fakes import MOCK_AGENT, MemorySessionStore

_WORKSPACE = "/tmp/ws"
_FS = StdioMcpServer(name="fs", command="mcp-fs", args=["--ro"], env={"FOO": "bar"})
_MEMORY = HttpMcpServer(name="memory", url="http://127.0.0.1:9/mcp", headers={"Authorization": "Bearer t0ken"})
_SERVERS = [_FS]
_HTTP_AGENT = AgentCapabilities(mcp_http=True)


def test_nothing_to_pass_is_none() -> None:
    assert acp_mcp_servers(None, _HTTP_AGENT).servers is None
    assert acp_mcp_servers([], _HTTP_AGENT).servers is None


def test_stdio_servers_are_converted_for_any_agent() -> None:
    converted = acp_mcp_servers(_SERVERS, AgentCapabilities())

    assert converted.servers is not None
    (server,) = converted.servers
    assert isinstance(server, McpServerStdio)
    assert (server.name, server.command, server.args) == ("fs", "mcp-fs", ["--ro"])
    assert [(variable.name, variable.value) for variable in server.env] == [("FOO", "bar")]
    assert converted.left_out == ()


def test_an_http_server_goes_to_an_agent_that_advertised_it_with_its_headers() -> None:
    converted = acp_mcp_servers([_MEMORY], _HTTP_AGENT)

    assert converted.servers is not None
    (server,) = converted.servers
    assert isinstance(server, AcpHttpMcpServer)
    assert (server.type, server.name, server.url) == ("http", "memory", "http://127.0.0.1:9/mcp")
    assert [(header.name, header.value) for header in server.headers] == [("Authorization", "Bearer t0ken")]
    assert converted.left_out == ()


def test_an_http_server_is_left_out_for_an_agent_that_did_not_advertise_it_and_the_stdio_ones_still_go() -> None:
    converted = acp_mcp_servers([_MEMORY, _FS], AgentCapabilities())

    assert converted.servers is not None
    assert [server.name for server in converted.servers] == ["fs"]
    assert converted.left_out == ("memory",)


def test_when_only_http_servers_are_left_out_nothing_is_handed_over() -> None:
    converted = acp_mcp_servers([_MEMORY], AgentCapabilities())

    assert converted.servers is None
    assert converted.left_out == ("memory",)


async def test_a_new_session_is_opened_with_the_servers() -> None:
    sessions = AgentSessions("claude", _WORKSPACE, None)
    conn = MagicMock()
    conn.new_session = AsyncMock(return_value=MagicMock(session_id="sess-1", modes=None))

    opened = await sessions.open(conn, "chat-1", AgentCapabilities(), _SERVERS)

    conn.new_session.assert_awaited_once()
    kwargs = conn.new_session.await_args.kwargs
    assert kwargs["cwd"] == _WORKSPACE
    assert len(kwargs["mcp_servers"]) == 1
    assert (opened.agent_session_id, sessions.agent_session("chat-1")) == ("sess-1", "sess-1")
    assert opened.mcp_left_out == ()


async def test_a_new_session_without_servers_leaves_the_sdk_default() -> None:
    sessions = AgentSessions("claude", _WORKSPACE, None)
    conn = MagicMock()
    conn.new_session = AsyncMock(return_value=MagicMock(session_id="sess-2", modes=None))

    await sessions.open(conn, "chat-1", AgentCapabilities(), None)

    conn.new_session.assert_awaited_once_with(cwd=_WORKSPACE, mcp_servers=None)


async def test_a_session_given_back_is_opened_with_the_servers_too() -> None:
    store = MemorySessionStore()
    store.records["chat-1"] = CliSessionRecord("sess-3", _WORKSPACE)
    sessions = AgentSessions("claude", _WORKSPACE, store)
    conn = MagicMock()
    conn.resume_session = AsyncMock(return_value=MagicMock(modes=None))

    opened = await sessions.open(conn, "chat-1", AgentCapabilities(resume_session=True), _SERVERS)

    kwargs = conn.resume_session.await_args.kwargs
    assert (kwargs["session_id"], kwargs["cwd"], len(kwargs["mcp_servers"])) == ("sess-3", _WORKSPACE, 1)
    assert opened.agent_session_id == "sess-3"


async def test_a_session_runs_without_the_left_out_servers_on_every_turn_not_only_the_first() -> None:
    sessions = AgentSessions("claude", _WORKSPACE, None)
    conn = MagicMock()
    conn.new_session = AsyncMock(return_value=MagicMock(session_id="sess-4", modes=None))

    first = await sessions.open(conn, "chat-1", AgentCapabilities(), [_MEMORY])
    second = await sessions.open(conn, "chat-1", AgentCapabilities(), [_MEMORY])

    assert (first.mcp_left_out, second.mcp_left_out) == (("memory",), ("memory",))
    conn.new_session.assert_awaited_once()


async def test_the_agent_process_being_gone_forgets_what_its_sessions_ran_without() -> None:
    sessions = AgentSessions("claude", _WORKSPACE, None)
    conn = MagicMock()
    conn.new_session = AsyncMock(return_value=MagicMock(session_id="sess-5", modes=None))
    await sessions.open(conn, "chat-1", AgentCapabilities(), [_MEMORY])

    sessions.agent_lost()
    reopened = await sessions.open(conn, "chat-1", _HTTP_AGENT, [_MEMORY])

    assert reopened.mcp_left_out == ()


# --- through the real ACP SDK, against the scripted agent -------------------------------------------------------


def _pool(*flags: str) -> RuntimePool:
    pool = RuntimePool(max_concurrent=1)
    pool.register(
        "mock",
        RuntimeConfig(backend_type="acp", command=sys.executable, args=[str(MOCK_AGENT), *flags], timeout_seconds=30),
    )
    return pool


async def _turn(
    pool: RuntimePool,
    workspace: Path,
    prompt: str,
    servers: list[McpServerConfig],
    permissions: PermissionBroker | None = None,
) -> list[RuntimeEvent]:
    return [
        event
        async for event in pool.run_turn(
            "mock", prompt, session_id="chat-1", workspace=str(workspace), mcp_servers=servers, permissions=permissions
        )
    ]


def _text(events: list[RuntimeEvent]) -> str:
    return "".join(str(e.data["content"]) for e in events if e.type is RuntimeEventType.TEXT_DELTA)


@pytest.mark.skipif(sys.platform == "win32", reason="the scripted agent is a POSIX subprocess")
async def test_an_agent_that_advertised_http_is_handed_the_server_over_the_wire(tmp_path: Path) -> None:
    pool = _pool("--mcp-http")
    try:
        events = await _turn(pool, tmp_path, "/mcp", [_MEMORY, _FS])
    finally:
        await pool.close_all()

    handed = json.loads(_text(events).removeprefix("[mcp] "))
    assert handed == [
        {
            "name": "memory",
            "url": "http://127.0.0.1:9/mcp",
            "headers": [{"name": "Authorization", "value": "Bearer t0ken"}],
            "type": "http",
        },
        {"name": "fs", "command": "mcp-fs", "args": ["--ro"], "env": [{"name": "FOO", "value": "bar"}]},
    ]
    assert not [e for e in events if e.data.get("status") == MCP_NOT_ATTACHED_STATUS]


def _withheld_names(events: list[RuntimeEvent]) -> list[str]:
    """The names of the servers the turn said it did not attach, in the order it said them."""
    names: list[str] = []
    for event in events:
        servers = event.data.get("servers")
        if event.data.get("status") == MCP_NOT_ATTACHED_STATUS and isinstance(servers, list):
            names.extend(str(name) for name in servers)
    return names


@pytest.mark.skipif(sys.platform == "win32", reason="the scripted agent is a POSIX subprocess")
async def test_an_agent_that_did_not_advertise_http_runs_without_it_and_the_turn_says_so(tmp_path: Path) -> None:
    pool = _pool()
    try:
        first = await _turn(pool, tmp_path, "/mcp", [_MEMORY, _FS])
        second = await _turn(pool, tmp_path, "/mcp", [_MEMORY, _FS])
    finally:
        await pool.close_all()

    for events in (first, second):
        assert _withheld_names(events) == ["memory"]
        assert [server["name"] for server in json.loads(_text(events).removeprefix("[mcp] "))] == ["fs"]


class _Broker:
    """The host: refuses whatever it is asked, and keeps what it was asked."""

    def __init__(self) -> None:
        self.asked: list[PermissionRequest] = []

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.asked.append(request)
        return PermissionVerdict(allowed=False)


_MCP_TOOL_CALL = (
    '/permission {"kind": "other", "title": "lookup(query: alpha)",'
    ' "meta": {"gemini-cli": {"mcp": {"server_name": "memory", "tool_name": "lookup"}}}}'
)


@pytest.mark.skipif(sys.platform == "win32", reason="the scripted agent is a POSIX subprocess")
async def test_a_tool_of_a_read_only_server_is_run_without_asking_and_any_other_is_asked_about(tmp_path: Path) -> None:
    read_only = HttpMcpServer(name="memory", url="http://127.0.0.1:9/mcp", read_only=True)
    pool = _pool("--mcp-http")
    broker = _Broker()
    try:
        said_yes = await _turn(pool, tmp_path, _MCP_TOOL_CALL, [read_only], broker)
        asked_about = await _turn(pool, tmp_path, _MCP_TOOL_CALL, [_MEMORY], broker)
    finally:
        await pool.close_all()

    assert _text(said_yes) == "[permission] selected allow-once"
    assert _text(asked_about) == "[permission] selected reject-once"
    assert [request.title for request in broker.asked] == ["lookup(query: alpha)"]
