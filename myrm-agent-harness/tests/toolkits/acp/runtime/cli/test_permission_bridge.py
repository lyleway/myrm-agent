"""The loopback MCP endpoint Claude Code asks its permission prompts through, driven over real HTTP.

The bridge is served by a real uvicorn on a loopback port; the tests speak MCP to it the way Claude Code does
(initialize, then ``tools/call`` of ``approve``) and read back the answer Claude Code would act on.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import stat
import sys
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest

from myrm_agent_harness.toolkits.acp.runtime.cli.permission_bridge import PERMISSION_PROMPT_TOOL, PermissionBridge
from myrm_agent_harness.toolkits.acp.runtime.turn_permissions import TurnPermissions
from myrm_agent_harness.toolkits.acp.types import PermissionBroker, PermissionRequest, PermissionVerdict

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="the config file's mode is checked with POSIX bits")

_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


class _Broker:
    def __init__(self, verdict: PermissionVerdict | Exception | None = None) -> None:
        self.verdict = verdict if verdict is not None else PermissionVerdict(allowed=True)
        self.asked: list[PermissionRequest] = []

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.asked.append(request)
        if isinstance(self.verdict, Exception):
            raise self.verdict
        return self.verdict


@asynccontextmanager
async def _bridge(broker: PermissionBroker) -> AsyncIterator[PermissionBridge]:
    async with asyncio.timeout(None) as clock:
        permissions = TurnPermissions(broker, clock)
        async with PermissionBridge(permissions, agent="claude", session_id="chat-1") as bridge:
            yield bridge


def _connection(bridge: PermissionBridge) -> tuple[str, dict[str, str]]:
    """The URL and headers Claude Code reads from the ``--mcp-config`` file."""
    config = json.loads(Path(bridge.config_path).read_text())
    server = config["mcpServers"]["myrm_permissions"]
    assert server["type"] == "http"
    return server["url"], {**_HEADERS, **server["headers"]}


async def _rpc(
    client: httpx.AsyncClient, url: str, headers: Mapping[str, str], method: str, params: Mapping[str, object]
) -> httpx.Response:
    return await client.post(url, headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})


async def _approve(
    bridge: PermissionBridge, tool_name: str, tool_input: Mapping[str, object], **extra: object
) -> dict[str, object]:
    """Call the ``approve`` tool like Claude Code, and parse the JSON the tool answered with."""
    url, headers = _connection(bridge)
    async with httpx.AsyncClient(timeout=30) as client:
        init = await _rpc(
            client,
            url,
            headers,
            "initialize",
            {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "claude-code", "version": "2"},
            },
        )
        assert init.status_code == 200, init.text
        call = await _rpc(
            client,
            url,
            headers,
            "tools/call",
            {"name": "approve", "arguments": {"tool_name": tool_name, "input": tool_input, **extra}},
        )
    assert call.status_code == 200, call.text
    (block,) = call.json()["result"]["content"]
    assert block["type"] == "text"
    answer: dict[str, object] = json.loads(block["text"])
    return answer


class TestTheApproveTool:
    async def test_an_allowed_request_is_answered_allow_with_the_input_unchanged(self) -> None:
        broker = _Broker()
        tool_input = {"command": "pytest -q", "description": "Run the tests"}

        async with _bridge(broker) as bridge:
            answer = await _approve(bridge, "Bash", tool_input, tool_use_id="toolu_1")

        assert answer == {"behavior": "allow", "updatedInput": tool_input}
        (request,) = broker.asked
        assert (request.agent, request.session_id, request.tool_name, request.kind) == (
            "claude",
            "chat-1",
            "Bash",
            "execute",
        )
        assert request.title == "Bash: Run the tests"
        assert request.input == tool_input
        assert request.tool_call_id == "toolu_1"

    async def test_a_refused_request_is_answered_deny_with_the_reason_the_model_will_read(self) -> None:
        broker = _Broker(PermissionVerdict(allowed=False, message="Not in this repository."))

        async with _bridge(broker) as bridge:
            answer = await _approve(bridge, "Bash", {"command": "rm -rf build"})

        assert answer == {"behavior": "deny", "message": "Not in this repository."}

    async def test_a_refusal_without_a_reason_still_says_why_nothing_ran(self) -> None:
        async with _bridge(_Broker(PermissionVerdict(allowed=False))) as bridge:
            answer = await _approve(bridge, "Bash", {"command": "ls"})

        assert answer == {"behavior": "deny", "message": "The user did not allow this action."}

    async def test_a_broker_that_fails_is_a_refusal_never_a_yes(self) -> None:
        async with _bridge(_Broker(RuntimeError("database is down"))) as bridge:
            answer = await _approve(bridge, "Write", {"file_path": "/work/a.txt", "content": "x"})

        assert answer["behavior"] == "deny"

    @pytest.mark.parametrize(
        ("tool_name", "tool_input", "kind", "paths", "title"),
        [
            (
                "Edit",
                {"file_path": "/work/a.py", "old_string": "a", "new_string": "b"},
                "edit",
                ("/work/a.py",),
                "Edit: /work/a.py",
            ),
            (
                "NotebookEdit",
                {"notebook_path": "/work/n.ipynb"},
                "edit",
                ("/work/n.ipynb",),
                "NotebookEdit: /work/n.ipynb",
            ),
            ("Read", {"file_path": "/work/a.py"}, "read", ("/work/a.py",), "Read: /work/a.py"),
            ("Grep", {"pattern": "x", "path": "/work"}, "search", ("/work",), "Grep: /work"),
            ("WebFetch", {"url": "https://example.com/x"}, "fetch", (), "WebFetch: https://example.com/x"),
            ("mcp__github__create_issue", {"title": "t"}, "other", (), "mcp__github__create_issue"),
            ("Bash", {"command": "line one\nline two"}, "execute", (), "Bash: line one"),
        ],
    )
    async def test_the_tool_is_described_to_the_broker_in_the_terms_the_host_knows(
        self, tool_name: str, tool_input: dict[str, object], kind: str, paths: tuple[str, ...], title: str
    ) -> None:
        broker = _Broker()

        async with _bridge(broker) as bridge:
            await _approve(bridge, tool_name, tool_input)

        (request,) = broker.asked
        assert (request.kind, request.paths, request.title) == (kind, paths, title)

    async def test_a_request_without_the_token_is_turned_away_before_the_broker_hears_of_it(self) -> None:
        broker = _Broker()

        async with _bridge(broker) as bridge:
            url, headers = _connection(bridge)
            async with httpx.AsyncClient(timeout=30) as client:
                missing = await _rpc(client, url, {**_HEADERS}, "tools/list", {})
                wrong = await _rpc(client, url, {**headers, "Authorization": "Bearer not-the-token"}, "tools/list", {})

        assert (missing.status_code, wrong.status_code) == (401, 401)
        assert broker.asked == []

    async def test_approve_is_the_only_tool_the_endpoint_offers(self) -> None:
        async with _bridge(_Broker()) as bridge:
            url, headers = _connection(bridge)
            async with httpx.AsyncClient(timeout=30) as client:
                await _rpc(
                    client,
                    url,
                    headers,
                    "initialize",
                    {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}},
                )
                listed = await _rpc(client, url, headers, "tools/list", {})

        assert [tool["name"] for tool in listed.json()["result"]["tools"]] == ["approve"]
        assert PERMISSION_PROMPT_TOOL == "mcp__myrm_permissions__approve"


class TestTheBridgeLifetime:
    async def test_the_config_file_is_private_to_the_user_and_gone_when_the_turn_is_over(self) -> None:
        async with _bridge(_Broker()) as bridge:
            path = Path(bridge.config_path)
            mode = stat.S_IMODE(os.stat(path).st_mode)
            assert mode == 0o600
            assert bridge.prompt_tool == PERMISSION_PROMPT_TOOL
        assert not path.exists()

    async def test_the_config_tells_claude_to_wait_for_a_person_for_hours_not_a_minute(self) -> None:
        async with _bridge(_Broker()) as bridge:
            config = json.loads(Path(bridge.config_path).read_text())

        # Claude refuses a prompt on its own after 60 s (and after 300 s of silence) unless the entry has a timeout.
        assert config["mcpServers"]["myrm_permissions"]["timeout"] >= 60 * 60 * 1000

    async def test_each_turn_gets_its_own_port_and_token(self) -> None:
        async with _bridge(_Broker()) as first, _bridge(_Broker()) as second:
            first_url, first_headers = _connection(first)
            second_url, second_headers = _connection(second)

        assert first_url != second_url
        assert first_headers["Authorization"] != second_headers["Authorization"]

    async def test_the_port_is_closed_when_the_turn_is_over(self) -> None:
        async with _bridge(_Broker()) as bridge:
            url, headers = _connection(bridge)
        async with httpx.AsyncClient(timeout=5) as client:
            with pytest.raises(httpx.ConnectError):
                await _rpc(client, url, headers, "tools/list", {})

    async def test_a_request_still_waiting_when_the_turn_ends_is_cancelled_and_the_bridge_stops_promptly(self) -> None:
        cancelled = asyncio.Event()
        asked = asyncio.Event()

        class _WaitingBroker:
            async def decide(self, request: PermissionRequest) -> PermissionVerdict:
                asked.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
                return PermissionVerdict(allowed=True)

        async def _call(bridge: PermissionBridge) -> None:
            # The endpoint goes away mid-request, which is what this test does to it.
            with contextlib.suppress(httpx.HTTPError, AssertionError, KeyError):
                await _approve(bridge, "Bash", {"command": "ls"})

        loop = asyncio.get_running_loop()
        async with _bridge(_WaitingBroker()) as bridge:
            call = asyncio.create_task(_call(bridge))
            await asyncio.wait_for(asked.wait(), 10)
            started = loop.time()
        stopped_in = loop.time() - started
        await asyncio.wait_for(call, 10)

        assert cancelled.is_set()
        assert stopped_in < 4
