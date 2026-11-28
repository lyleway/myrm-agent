"""Which external agents are given the user's memory: those the user shared it with, and only to read.

[POS]
Tests for the ``shareMemory`` entry of an external agent config in
app/ai_agents/general_agent/external_agents_runtime_config.py: only an explicit ``true`` is a consent, the agent it
reaches is given the read-only memory server of the Myrm agent the pool belongs to, and a pool built for other memory
is a different pool.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from myrm_agent_harness.toolkits.acp.mcp_servers import HttpMcpServer
from myrm_agent_harness.toolkits.acp.types import RuntimeConfig

from app.ai_agents.general_agent.external_agents import ExternalAgentsMixin
from app.ai_agents.general_agent.external_agents_runtime_config import _config_fingerprint, _register_backends_on_pool
from app.config.settings import settings
from app.services.external_agents.memory_grants import MEMORY_SERVER_NAME, get_memory_grants

MYRM_AGENT = "agent-7"


def _register(
    configs: list[dict[str, object]], *, memory_agent_id: str | None = MYRM_AGENT
) -> tuple[MagicMock, dict[str, RuntimeConfig]]:
    """The pool (a credential lasts only as long as its pool does) and the runtime config each agent was registered with."""
    pool = MagicMock()
    _register_backends_on_pool(pool, configs, memory_agent_id=memory_agent_id)
    return pool, {call.args[0]: call.args[1] for call in pool.register.call_args_list}


def _memory_server(config: RuntimeConfig) -> HttpMcpServer:
    (server,) = config.mcp_servers
    assert isinstance(server, HttpMcpServer)
    return server


def _agent(name: str = "claude", **extra: object) -> dict[str, object]:
    return {"name": name, "command": name, **extra}


class TestWhatAnAgentIsGiven:
    def test_an_agent_the_user_shared_memory_with_is_given_the_read_only_memory_server(self) -> None:
        pool, registered = _register([_agent("gemini", shareMemory=True)])

        pool.register.assert_called_once()
        server = _memory_server(registered["gemini"])
        grant = get_memory_grants().peek(server.headers["Authorization"].removeprefix("Bearer "))

        assert (server.name, server.read_only) == (MEMORY_SERVER_NAME, True)
        assert server.url == f"http://127.0.0.1:{settings.port}/mcp/"
        assert grant is not None
        assert (grant.agent_id, grant.external_agent) == (MYRM_AGENT, "gemini")

    @pytest.mark.parametrize("consent", [False, None, "true", "yes", 1, 0, [True], {}])
    def test_anything_but_an_explicit_true_is_no_consent(self, consent: object) -> None:
        _, registered = _register([_agent(shareMemory=consent)])

        assert registered["claude"].mcp_servers == ()

    def test_an_agent_with_no_entry_is_given_nothing(self) -> None:
        _, registered = _register([_agent()])

        assert registered["claude"].mcp_servers == ()

    def test_a_pool_that_belongs_to_no_agent_has_no_memory_to_give(self) -> None:
        _, registered = _register([_agent(shareMemory=True)], memory_agent_id=None)

        assert registered["claude"].mcp_servers == ()

    def test_only_the_agents_the_user_chose_are_given_memory_and_each_has_its_own_credential(self) -> None:
        _, registered = _register([_agent("gemini", shareMemory=True), _agent("qwen", shareMemory=True), _agent("codex")])

        gemini, qwen = _memory_server(registered["gemini"]), _memory_server(registered["qwen"])

        assert registered["codex"].mcp_servers == ()
        assert gemini.headers != qwen.headers


class TestWhichPoolIsWhich:
    def test_sharing_memory_makes_a_different_pool(self) -> None:
        shared = _config_fingerprint([_agent(shareMemory=True)], memory_agent_id=MYRM_AGENT)
        private = _config_fingerprint([_agent()], memory_agent_id=MYRM_AGENT)

        assert shared != private

    def test_a_pool_for_another_agents_memory_is_a_different_pool_when_memory_is_shared(self) -> None:
        mine = _config_fingerprint([_agent(shareMemory=True)], memory_agent_id="agent-1")
        theirs = _config_fingerprint([_agent(shareMemory=True)], memory_agent_id="agent-2")

        assert mine != theirs

    def test_the_agent_owning_a_pool_does_not_matter_when_no_memory_is_shared(self) -> None:
        one = _config_fingerprint([_agent()], memory_agent_id="agent-1")
        other = _config_fingerprint([_agent()], memory_agent_id="agent-2")

        assert one == other

    def test_a_pool_with_no_owner_is_the_pool_that_shares_nothing(self) -> None:
        assert _config_fingerprint([_agent(shareMemory=True)]) == _config_fingerprint([_agent()])


class TestWhoseMemoryItIs:
    @staticmethod
    async def _whose_memory_the_pool_of(agent_id: str | None) -> tuple[str, str]:
        """The Myrm agent whose memory, and the external agent for which, the credential in a freshly built pool is."""
        mixin = ExternalAgentsMixin.__new__(ExternalAgentsMixin)
        mixin.external_agents_config = [_agent("gemini", shareMemory=True)]
        mixin.chat_id = None
        mixin.agent_id = agent_id
        mixin._runtime_pool_scope_id = None

        await mixin._do_setup_external_agents([], mount_invoke_acp_agent_tool=False)

        pool = mixin._owned_runtime_pool
        assert pool is not None
        config = pool.get_config("gemini")
        assert config is not None
        grant = get_memory_grants().peek(_memory_server(config).headers["Authorization"].removeprefix("Bearer "))
        assert grant is not None
        return grant.agent_id, grant.external_agent

    async def test_an_agent_in_the_pool_of_a_myrm_agent_is_given_that_agents_memory(self) -> None:
        assert await self._whose_memory_the_pool_of("agent-9") == ("agent-9", "gemini")

    async def test_the_default_agent_is_the_one_whose_memory_a_pool_with_no_agent_gives(self) -> None:
        memory_of, _ = await self._whose_memory_the_pool_of(None)

        assert memory_of == "default"
