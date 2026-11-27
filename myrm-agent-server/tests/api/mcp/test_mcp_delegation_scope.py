"""The /mcp endpoint for the memory credential of a delegated agent: it reads the memory it was issued for, and nothing else.

Runs the real endpoint (the SDK server, its listing and its call path, the auth middleware); only the user's data is
stubbed.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import Tool

from app.api.mcp.delegation_scope import READ_ONLY_TOOLS, read_only_request, readable_only, require_readable
from app.api.mcp.endpoint import setup_mcp_endpoint, shutdown_mcp_endpoint
from app.services.connect.service import ConnectService
from app.services.external_agents.memory_grants import get_memory_grants, memory_server_for

if TYPE_CHECKING:
    from httpx2 import Response  # the response type starlette's TestClient returns

AGENT_ID = "agent-mem"

READING_TOOLS = {"memory_recall", "memory_list"}
WRITING_TOOLS = {"memory_store", "memory_manage"}


class _Pool:
    """Stands for the external-agent pool a credential belongs to."""


@dataclass
class _Endpoint:
    client: TestClient
    memory: AsyncMock
    manager_for_agent: AsyncMock
    wiki_tools: AsyncMock
    service: ConnectService
    connect_token: str
    delegation_token: str
    pool: _Pool

    def post(self, token: str, method: str, params: dict[str, object] | None = None) -> Response:
        return self.client.post(
            "/mcp",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
        )

    def wiki_read_only(self) -> object:
        """The ``read_only`` the wiki bundle of the latest request was built with."""
        call = self.wiki_tools.await_args
        assert call is not None
        return call.kwargs["read_only"]

    def tool_names(self, token: str) -> set[str]:
        response = self.post(token, "tools/list")
        assert response.status_code == 200
        return {tool["name"] for tool in response.json()["result"]["tools"]}

    def call(self, token: str, name: str, arguments: dict[str, object]) -> dict[str, object]:
        response = self.post(token, "tools/call", {"name": name, "arguments": arguments})
        assert response.status_code == 200
        result: dict[str, object] = response.json()["result"]
        return result


def _text_of(result: dict[str, object]) -> str:
    content = result["content"]
    assert isinstance(content, list)
    return " ".join(str(part["text"]) for part in content)


@asynccontextmanager
async def _endpoint(tmp_path: Path, *, wiki: bool) -> AsyncIterator[_Endpoint]:
    service = ConnectService(data_dir=tmp_path)
    snippet = await service.generate_config("cursor", agent_id=AGENT_ID, expose_desktop=False)
    memory = AsyncMock()
    memory.search = AsyncMock(return_value=[])
    memory.last_retrieval_trace = None
    manager_for_agent = AsyncMock(return_value=memory)
    wiki_tools = AsyncMock(return_value={"wiki_query_tool": MagicMock()} if wiki else None)
    pool = _Pool()
    server = memory_server_for(pool, agent_id=AGENT_ID, external_agent="gemini")

    async with AsyncExitStack() as stack:
        for target, replacement in (
            ("app.services.connect.get_connect_service", MagicMock(return_value=service)),
            ("app.api.mcp.endpoint._memory_manager_for_agent", manager_for_agent),
            ("app.api.mcp.endpoint.wiki_boundary_enabled_for_agent", AsyncMock(return_value=wiki)),
            ("app.api.mcp.endpoint.wiki_tools_for_agent", wiki_tools),
            ("app.api.mcp.endpoint.is_desktop_control_enabled_for_agent", AsyncMock(return_value=False)),
        ):
            stack.enter_context(patch(target, replacement))
        app = FastAPI()
        await setup_mcp_endpoint(app)
        try:
            yield _Endpoint(
                client=TestClient(app, base_url="http://localhost:8080"),  # the MCP guards need a loopback Host with a port
                memory=memory,
                manager_for_agent=manager_for_agent,
                wiki_tools=wiki_tools,
                service=service,
                connect_token=snippet.token,
                delegation_token=server.headers["Authorization"].removeprefix("Bearer "),
                pool=pool,
            )
        finally:
            await shutdown_mcp_endpoint()
            get_memory_grants().revoke(pool)


class TestWhatTheCredentialLists:
    async def test_it_lists_the_reading_memory_tools_and_no_others(self, tmp_path: Path) -> None:
        async with _endpoint(tmp_path, wiki=False) as endpoint:
            assert endpoint.tool_names(endpoint.delegation_token) == READING_TOOLS

    async def test_it_also_lists_the_wiki_query_when_the_agent_has_a_wiki(self, tmp_path: Path) -> None:
        async with _endpoint(tmp_path, wiki=True) as endpoint:
            assert endpoint.tool_names(endpoint.delegation_token) == READING_TOOLS | {"wiki_query_tool"}

    async def test_the_wiki_it_reaches_does_not_file_answers_back_but_a_connected_clients_does(self, tmp_path: Path) -> None:
        async with _endpoint(tmp_path, wiki=True) as endpoint:
            endpoint.tool_names(endpoint.delegation_token)
            assert endpoint.wiki_read_only() is True

            endpoint.tool_names(endpoint.connect_token)
            assert endpoint.wiki_read_only() is False

    async def test_a_connected_client_keeps_every_tool(self, tmp_path: Path) -> None:
        async with _endpoint(tmp_path, wiki=True) as endpoint:
            names = endpoint.tool_names(endpoint.connect_token)

            assert names >= READING_TOOLS | WRITING_TOOLS | {"wiki_query_tool", "wiki_ingest_tool", "wiki_apply_tool"}

    async def test_every_tool_the_credential_may_use_is_a_tool_the_endpoint_has(self, tmp_path: Path) -> None:
        """A renamed or removed tool must not leave a stale name in the allowlist."""
        async with _endpoint(tmp_path, wiki=True) as endpoint:
            assert READ_ONLY_TOOLS <= endpoint.tool_names(endpoint.connect_token)


class TestWhatTheCredentialMayCall:
    async def test_it_reads_the_memory_it_was_issued_for(self, tmp_path: Path) -> None:
        async with _endpoint(tmp_path, wiki=False) as endpoint:
            result = endpoint.call(endpoint.delegation_token, "memory_recall", {"query": "my editor"})

            assert not result.get("isError")
            endpoint.memory.search.assert_awaited_once()

    @pytest.mark.parametrize(
        ("tool", "arguments"),
        [
            ("memory_store", {"content": "I like tabs"}),
            ("memory_manage", {"action": "delete", "memory_id": "m1"}),
            ("wiki_ingest_tool", {"source": "some notes"}),
            ("wiki_apply_tool", {"op": "create_note", "concept_name": "notes/x", "body": "text"}),
            ("desktop_interact_tool", {"action": "click"}),
            ("a_tool_that_does_not_exist", {}),
        ],
    )
    async def test_it_cannot_call_a_tool_it_was_not_listed_even_by_name(
        self, tmp_path: Path, tool: str, arguments: dict[str, object]
    ) -> None:
        async with _endpoint(tmp_path, wiki=True) as endpoint:
            result = endpoint.call(endpoint.delegation_token, tool, arguments)

            assert result["isError"] is True
            assert "only reads memory" in _text_of(result)
            assert endpoint.memory.mock_calls == []

    async def test_a_connected_client_can_still_write(self, tmp_path: Path) -> None:
        async with _endpoint(tmp_path, wiki=False) as endpoint:
            result = endpoint.call(endpoint.connect_token, "memory_store", {"content": "I like tabs"})

            assert "only reads memory" not in _text_of(result)
            assert endpoint.memory.mock_calls != []


class TestWhoTheCredentialIs:
    async def test_it_reaches_the_memory_of_the_agent_it_was_issued_for(self, tmp_path: Path) -> None:
        async with _endpoint(tmp_path, wiki=False) as endpoint:
            endpoint.tool_names(endpoint.delegation_token)

            endpoint.manager_for_agent.assert_awaited_once_with(AGENT_ID)

    async def test_using_it_counts_as_the_agent_connecting_and_marks_no_connect_profile_ready(self, tmp_path: Path) -> None:
        async with _endpoint(tmp_path, wiki=False) as endpoint:
            grant = get_memory_grants().peek(endpoint.delegation_token)
            assert grant is not None
            assert await grant.wait_connected(0.01) is False

            with patch.object(endpoint.service, "mark_ready") as mark_ready:
                endpoint.tool_names(endpoint.delegation_token)

            assert await grant.wait_connected(0.01) is True
            mark_ready.assert_not_called()

    async def test_a_credential_that_has_ended_is_refused(self, tmp_path: Path) -> None:
        async with _endpoint(tmp_path, wiki=False) as endpoint:
            get_memory_grants().revoke(endpoint.pool)

            response = endpoint.post(endpoint.delegation_token, "tools/list")

            assert response.status_code == 403

    async def test_a_credential_nobody_issued_is_refused(self, tmp_path: Path) -> None:
        async with _endpoint(tmp_path, wiki=False) as endpoint:
            response = endpoint.post("myrm_dlg_never_issued", "tools/list")

            assert response.status_code == 403


class TestTwoRequestsAtOnce:
    """Requests served at the same moment each keep the scope of their own credential."""

    async def test_each_keeps_its_own_listing_and_its_own_refusals(self) -> None:
        tools = [Tool(name=name, inputSchema={"type": "object"}) for name in sorted(READING_TOOLS | WRITING_TOOLS)]
        both_in_flight = asyncio.Barrier(2)

        async def serve(*, read_only: bool) -> tuple[list[str], bool]:
            with read_only_request(read_only):
                await both_in_flight.wait()  # both have set their scope before either looks
                listed = [tool.name for tool in readable_only(tools)]
                try:
                    require_readable("memory_store")
                    refused = False
                except ToolError:
                    refused = True
                await both_in_flight.wait()  # and neither leaves before the other has looked
                return listed, refused

        delegated, connected = await asyncio.gather(serve(read_only=True), serve(read_only=False))

        assert delegated == (sorted(READING_TOOLS), True)
        assert connected == (sorted(READING_TOOLS | WRITING_TOOLS), False)
