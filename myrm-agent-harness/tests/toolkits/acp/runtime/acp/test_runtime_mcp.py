"""MCP servers reach an ACP session as stdio servers, whether the session is new or given back."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from myrm_agent_harness.toolkits.acp.runtime.acp.capabilities import AgentCapabilities
from myrm_agent_harness.toolkits.acp.runtime.acp.sessions import AgentSessions, acp_mcp_servers
from myrm_agent_harness.toolkits.acp.types import CliSessionRecord, McpServerConfig

from ._fakes import MemorySessionStore

_WORKSPACE = "/tmp/ws"
_SERVERS = [McpServerConfig(name="fs", command="mcp-fs", args=["--ro"], env={"FOO": "bar"})]


def test_nothing_to_pass_is_none() -> None:
    assert acp_mcp_servers(None) is None
    assert acp_mcp_servers([]) is None


def test_stdio_servers_are_converted() -> None:
    converted = acp_mcp_servers(_SERVERS)

    assert converted is not None
    (server,) = converted
    assert (server.name, server.command, server.args) == ("fs", "mcp-fs", ["--ro"])
    assert [(variable.name, variable.value) for variable in server.env] == [("FOO", "bar")]


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
