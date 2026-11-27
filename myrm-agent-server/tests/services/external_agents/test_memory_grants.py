"""The memory credentials of delegated external agents: what a credential reaches, how long it lives, what it hides."""

from __future__ import annotations

import asyncio
import gc
from collections.abc import Iterator

import pytest
from myrm_agent_harness.toolkits.acp.mcp_servers import HttpMcpServer, StdioMcpServer
from myrm_agent_harness.toolkits.acp.types import RuntimeConfig

from app.config.settings import settings
from app.services.external_agents import memory_grants
from app.services.external_agents.memory_grants import (
    MEMORY_SERVER_NAME,
    MemoryGrants,
    get_memory_grants,
    memory_connected,
    memory_server_for,
)


class _Owner:
    """Stands for a pool: anything a credential can be tied to."""


def _token_of(server: HttpMcpServer) -> str:
    return server.headers["Authorization"].removeprefix("Bearer ")


def _config(*servers: HttpMcpServer | StdioMcpServer) -> RuntimeConfig:
    return RuntimeConfig(backend_type="cli", mcp_servers=servers)


class TestCredentials:
    def test_a_credential_reads_the_memory_it_was_issued_for(self) -> None:
        grants = MemoryGrants()
        owner = _Owner()
        token = grants.issue(owner, agent_id="agent-7", external_agent="gemini")

        grant = grants.resolve(token)

        assert grant is not None
        assert (grant.agent_id, grant.external_agent) == ("agent-7", "gemini")

    def test_every_credential_is_its_own(self) -> None:
        grants = MemoryGrants()
        owner = _Owner()
        first = grants.issue(owner, agent_id="a", external_agent="gemini")
        second = grants.issue(owner, agent_id="a", external_agent="qwen")

        assert first != second
        assert grants.resolve(first).external_agent == "gemini"  # type: ignore[union-attr]
        assert grants.resolve(second).external_agent == "qwen"  # type: ignore[union-attr]

    @pytest.mark.parametrize("token", ["", "myrm_dlg_guess", "myrm_mcp_a-connect-token"])
    def test_a_credential_that_was_never_issued_reaches_nothing(self, token: str) -> None:
        grants = MemoryGrants()
        owner = _Owner()
        grants.issue(owner, agent_id="a", external_agent="gemini")

        assert grants.resolve(token) is None
        assert grants.peek(token) is None

    def test_only_a_digest_of_a_credential_is_kept(self) -> None:
        grants = MemoryGrants()
        owner = _Owner()
        token = grants.issue(owner, agent_id="a", external_agent="gemini")

        assert token not in repr(vars(grants))
        assert not any(token in key for key in vars(grants)["_grants"])


class TestLifetime:
    def test_a_pool_ending_ends_its_credentials_and_only_those(self) -> None:
        grants = MemoryGrants()
        closing, staying = _Owner(), _Owner()
        gone = [grants.issue(closing, agent_id="a", external_agent=name) for name in ("gemini", "qwen")]
        kept = grants.issue(staying, agent_id="a", external_agent="gemini")

        grants.revoke(closing)

        assert [grants.resolve(token) for token in gone] == [None, None]
        assert grants.resolve(kept) is not None

    def test_revoking_twice_or_an_owner_with_nothing_issued_is_harmless(self) -> None:
        grants = MemoryGrants()
        owner = _Owner()
        grants.issue(owner, agent_id="a", external_agent="gemini")

        grants.revoke(owner)
        grants.revoke(owner)
        grants.revoke(_Owner())

    def test_a_pool_dropped_without_being_closed_takes_its_credentials_with_it(self) -> None:
        grants = MemoryGrants()
        owner = _Owner()
        token = grants.issue(owner, agent_id="a", external_agent="gemini")

        del owner
        gc.collect()

        assert grants.resolve(token) is None


class TestConnection:
    async def test_using_a_credential_counts_as_connecting_but_looking_it_up_does_not(self) -> None:
        grants = MemoryGrants()
        owner = _Owner()
        token = grants.issue(owner, agent_id="a", external_agent="gemini")
        grant = grants.peek(token)
        assert grant is not None

        assert await grant.wait_connected(0.01) is False

        grants.resolve(token)

        assert await grant.wait_connected(0.01) is True


class TestMemoryServer:
    def test_the_server_handed_to_an_agent_is_this_servers_endpoint_with_a_credential_that_only_reads(self) -> None:
        owner = _Owner()
        try:
            server = memory_server_for(owner, agent_id="agent-7", external_agent="gemini")
            grant = get_memory_grants().peek(_token_of(server))

            assert server.name == MEMORY_SERVER_NAME
            assert server.url == f"http://127.0.0.1:{settings.port}/mcp/"
            assert server.read_only is True
            assert grant is not None
            assert (grant.agent_id, grant.external_agent) == ("agent-7", "gemini")
        finally:
            get_memory_grants().revoke(owner)

    def test_a_logged_server_does_not_show_its_credential(self) -> None:
        owner = _Owner()
        try:
            server = memory_server_for(owner, agent_id="a", external_agent="gemini")

            assert _token_of(server) not in repr(server)
        finally:
            get_memory_grants().revoke(owner)


class TestMemoryConnected:
    @pytest.fixture(autouse=True)
    def _short_grace(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(memory_grants, "_CONNECT_GRACE_SECONDS", 0.05)

    @pytest.fixture
    def owner(self) -> Iterator[_Owner]:
        owner = _Owner()
        yield owner
        get_memory_grants().revoke(owner)

    async def test_an_agent_given_no_memory_has_nothing_to_connect(self) -> None:
        plain = StdioMcpServer(name="fs", command="mcp-fs")

        assert await memory_connected(None) is None
        assert await memory_connected(_config()) is None
        assert await memory_connected(_config(plain)) is None

    async def test_an_agent_that_used_its_credential_is_connected(self, owner: _Owner) -> None:
        server = memory_server_for(owner, agent_id="a", external_agent="gemini")
        get_memory_grants().resolve(_token_of(server))

        assert await memory_connected(_config(server)) is True

    async def test_an_agent_that_never_used_its_credential_is_not_connected(self, owner: _Owner) -> None:
        server = memory_server_for(owner, agent_id="a", external_agent="gemini")

        assert await memory_connected(_config(server)) is False

    async def test_an_agent_that_connects_while_the_turn_is_ending_is_connected(
        self, owner: _Owner, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(memory_grants, "_CONNECT_GRACE_SECONDS", 2.0)
        server = memory_server_for(owner, agent_id="a", external_agent="gemini")
        loop = asyncio.get_running_loop()
        loop.call_later(0.02, get_memory_grants().resolve, _token_of(server))

        assert await memory_connected(_config(server)) is True

    async def test_a_credential_that_has_ended_is_not_connected(self, owner: _Owner) -> None:
        server = memory_server_for(owner, agent_id="a", external_agent="gemini")
        get_memory_grants().resolve(_token_of(server))
        get_memory_grants().revoke(owner)

        assert await memory_connected(_config(server)) is False
