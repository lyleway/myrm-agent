"""A real agent handed an MCP server over HTTP connects with its credential, calls its tool and asks about the call only if it must.

The host (memory-follow) gives every delegated agent an endpoint of its own behind a bearer token. Each of the four
agents takes it in its own form (Claude: a ``--mcp-config`` file; Codex: the config of a thread; Gemini and Qwen: an ACP
``session/new``) and decides for itself whether a call to it needs the person's yes. The models are scripted
(``FakeAnthropicApi``, ``FakeResponsesApi``, ``FakeGeminiApi``, ``FakeOpenAiChatApi``) and the endpoint is a real
streamable-HTTP MCP server that keeps every request (``RecordingMcpEndpoint``); everything else is real: the binary, the
form the runtime hands the server over in, the connection with the token, the call, and the permission request.
A release that changes any of those fails here and not in a user's chat.

What it pins: the tool is called with the credential, and the host is asked about the call exactly when the server is
not declared ``read_only`` (the agent's own permission prompt for Claude and Codex, the harness's rule for Gemini and
Qwen). Gemini also never connects in a workspace it does not trust, which is why a host observes the connection and does
not assume it.

Run with ``-m integration``. An agent that is not installed skips its tests, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names
it. No login and no real model are involved.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from myrm_agent_harness.toolkits.acp.mcp_servers import HttpMcpServer
from myrm_agent_harness.toolkits.acp.runtime.cli.runtime import CliRuntime
from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import RuntimeConfig, RuntimeEvent, RuntimeEventType
from tests.toolkits.acp._brokers import AnsweringBroker
from tests.toolkits.acp._fake_anthropic import FakeAnthropicApi
from tests.toolkits.acp._fake_model_apis import FakeResponsesApi, ToolCall
from tests.toolkits.acp._http_mcp_server import RecordingMcpEndpoint
from tests.toolkits.acp.cli_contract import cli_binary
from tests.toolkits.acp.runtime.acp._real_agents import SCRIPTED_AGENTS, scripted_pool
from tests.toolkits.acp.runtime.codex._real import codex_pool, write_codex_home

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(sys.platform == "win32", reason="the real agents are driven as POSIX subprocesses"),
]

_QUERY: Mapping[str, object] = {"query": "alpha"}
_PROMPT = "look it up"
_TURN_SECONDS = 90
# The tool calls a scripted model makes to reach ``lookup`` of the server ``memory``. Qwen hides the tools of an MCP
# server behind ``tool_search`` and ``tool_call``; Gemini names the tool ``mcp_<server>_<tool>``.
_ACP_CALLS: Mapping[str, Sequence[ToolCall]] = {
    "gemini": [ToolCall("mcp_memory_lookup", _QUERY)],
    "qwen": [
        ToolCall("tool_search", {"query": "select:mcp__memory__lookup"}),
        ToolCall("tool_call", {"name": "mcp__memory__lookup", "arguments": _QUERY}),
    ],
}
# Gemini connects to no MCP server in a workspace it does not trust, and a process without a terminal never asks.
_ACP_ENV: Mapping[str, Mapping[str, str]] = {"gemini": {"GEMINI_CLI_TRUST_WORKSPACE": "true"}, "qwen": {}}
# Qwen answers ``session/new`` before it has connected to the server (its first request comes about 0.15 s later), and
# the turn that starts at once does not find the server's tool: the first turn of a chat is spent on opening it. It also
# gives a remote server five seconds from the start of its discovery and drops it for the session after that, which a
# machine too busy to start the agent's own work in time brings about; the test then starts over with a session of its
# own, and fails only if no session gets the server listed.
_CONNECTS_AFTER_THE_SESSION_OPENS = frozenset({"qwen"})
_CONNECT_ATTEMPTS = 3
_LISTED_WITHIN_SECONDS = 5


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


def _server(endpoint: RecordingMcpEndpoint, *, read_only: bool) -> HttpMcpServer:
    return HttpMcpServer(
        name="memory",
        url=endpoint.url,
        headers={"Authorization": f"Bearer {endpoint.token}"},
        read_only=read_only,
    )


def _ended_cleanly(events: list[RuntimeEvent]) -> bool:
    types = [event.type for event in events]
    return RuntimeEventType.ERROR not in types and types[-1] is RuntimeEventType.DONE


def _called_with_the_token(endpoint: RecordingMcpEndpoint) -> None:
    assert [(call.tool, call.arguments) for call in endpoint.tool_calls] == [("lookup", dict(_QUERY))]
    assert all(request.authorized for request in endpoint.requests)


@pytest.mark.parametrize("read_only", [True, False])
async def test_claude_calls_the_tool_and_asks_about_it_only_when_the_server_is_not_read_only(
    tmp_path: Path, workspace: Path, read_only: bool
) -> None:
    binary = cli_binary("claude")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    broker = AnsweringBroker(True)
    async with RecordingMcpEndpoint() as endpoint, FakeAnthropicApi("mcp__memory__lookup", _QUERY) as api:
        config = RuntimeConfig(
            backend_type="cli",
            command=binary,
            auth_mode="api_key",
            env={
                "ANTHROPIC_API_KEY": "sk-ant-api03-fake",
                "ANTHROPIC_BASE_URL": api.base_url,
                "HOME": str(home),
                "CLAUDE_CONFIG_DIR": str(home / ".claude"),
                "DISABLE_AUTOUPDATER": "1",
                "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
                # Claude puts the tools of an MCP server behind a search tool when it judges them many; the scripted
                # model does not search.
                "ENABLE_TOOL_SEARCH": "false",
            },
            timeout_seconds=_TURN_SECONDS,
            max_turns=3,
        )
        runtime = CliRuntime("claude", config, workspace=str(workspace))
        events = [
            event
            async for event in runtime.run_turn(
                _PROMPT, "s1", mcp_servers=[_server(endpoint, read_only=read_only)], permissions=broker
            )
        ]

    assert _ended_cleanly(events)
    _called_with_the_token(endpoint)
    assert "found:alpha" in json.dumps(api.tool_results)
    assert [(request.tool_name, request.kind) for request in broker.asked] == (
        [] if read_only else [("mcp__memory__lookup", "other")]
    )


@pytest.mark.parametrize("read_only", [True, False])
async def test_codex_calls_the_tool_and_asks_about_it_only_when_the_server_is_not_read_only(
    tmp_path: Path, workspace: Path, read_only: bool
) -> None:
    broker = AnsweringBroker(True)
    call = ToolCall("lookup", _QUERY, namespace="mcp__memory")
    async with RecordingMcpEndpoint() as endpoint, FakeResponsesApi(calls=[call]) as api:
        pool = codex_pool(
            write_codex_home(tmp_path, api.base_url), mcp_servers=[_server(endpoint, read_only=read_only)]
        )
        try:
            events = [
                event
                async for event in pool.run_turn(
                    "codex", _PROMPT, "chat-1", workspace=str(workspace), permissions=broker
                )
            ]
        finally:
            await pool.close_all()

    assert _ended_cleanly(events)
    _called_with_the_token(endpoint)
    assert "found:alpha" in json.dumps(api.tool_results)
    assert [request.tool_name for request in broker.asked] == ([] if read_only else ["mcp__memory__lookup"])


async def _chat_turn(
    pool: RuntimePool, agent: str, workspace: Path, broker: AnsweringBroker, server: HttpMcpServer
) -> list[RuntimeEvent]:
    return [
        event
        async for event in pool.run_turn(
            agent, _PROMPT, "chat-1", workspace=str(workspace), permissions=broker, mcp_servers=[server]
        )
    ]


async def _acp_turn(
    agent: str, tmp_path: Path, workspace: Path, endpoint: RecordingMcpEndpoint, *, read_only: bool, trusted: bool
) -> tuple[list[RuntimeEvent], AnsweringBroker, list[str]]:
    """The turn of the real ``agent`` over ACP in which the model asks for ``_ACP_CALLS[agent]``."""
    broker = AnsweringBroker(True)
    server = _server(endpoint, read_only=read_only)
    waits_for_the_server = agent in _CONNECTS_AFTER_THE_SESSION_OPENS
    attempts = _CONNECT_ATTEMPTS if waits_for_the_server else 1
    for attempt in range(attempts):
        home = tmp_path / f"home-{attempt}"
        home.mkdir()
        async with SCRIPTED_AGENTS[agent].api() as api:
            pool = scripted_pool(agent, api, home, extra_env=_ACP_ENV[agent] if trusted else {})
            try:
                if waits_for_the_server:
                    await _chat_turn(pool, agent, workspace, broker, server)
                    if not await endpoint.listed_tools_within(_LISTED_WITHIN_SECONDS):
                        continue
                api.calls = _ACP_CALLS[agent]
                events = await _chat_turn(pool, agent, workspace, broker, server)
                return events, broker, api.tool_results
            finally:
                await pool.close_all()
    pytest.fail(f"{agent} listed no tools of the server in {attempts} sessions of its own")


@pytest.mark.parametrize("read_only", [True, False])
@pytest.mark.parametrize("agent", sorted(_ACP_CALLS))
async def test_an_acp_agent_calls_the_tool_and_is_asked_about_only_when_the_server_is_not_read_only(
    agent: str, tmp_path: Path, workspace: Path, read_only: bool
) -> None:
    async with RecordingMcpEndpoint() as endpoint:
        events, broker, tool_results = await _acp_turn(
            agent, tmp_path, workspace, endpoint, read_only=read_only, trusted=True
        )

    assert _ended_cleanly(events)
    _called_with_the_token(endpoint)
    assert "found:alpha" in json.dumps(tool_results)
    assert [request.kind for request in broker.asked] == ([] if read_only else ["other"])


async def test_gemini_never_connects_to_the_server_in_a_workspace_it_does_not_trust(
    tmp_path: Path, workspace: Path
) -> None:
    async with RecordingMcpEndpoint() as endpoint:
        events, broker, tool_results = await _acp_turn(
            "gemini", tmp_path, workspace, endpoint, read_only=True, trusted=False
        )

    assert _ended_cleanly(events)
    assert endpoint.requests == []
    assert (broker.asked, tool_results) == ([], [])
