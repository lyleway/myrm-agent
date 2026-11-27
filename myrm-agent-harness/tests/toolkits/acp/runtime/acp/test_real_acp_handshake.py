"""The real ACP agents still answer ``initialize`` with what the runtime negotiates. Offline: no login, no model.

Each agent is started the way ``AcpRuntime`` starts one and asked the same ``initialize`` question; the answer is judged
by ``handshake_gaps``, whose sensitivity is pinned in ``test_acp_contract_judge.py``.

Run with ``-m integration``. An agent that is not installed skips its test, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.schema import Implementation, InitializeResponse

from myrm_agent_harness.toolkits.acp.runtime.acp.callback import AcpCallbackHandler, client_capabilities
from myrm_agent_harness.toolkits.acp.types import RuntimeConfig
from tests.toolkits.acp.acp_contract import ACP_ARGS, handshake_gaps
from tests.toolkits.acp.cli_contract import cli_binary, probe_env

pytestmark = pytest.mark.integration

_HANDSHAKE_TIMEOUT_SECONDS = 90


async def _initialize(agent: str, home: Path) -> InitializeResponse:
    binary = cli_binary(agent)
    handler = AcpCallbackHandler(RuntimeConfig(backend_type="acp", command=binary), agent=binary, workspace=str(home))
    async with asyncio.timeout(_HANDSHAKE_TIMEOUT_SECONDS):
        async with spawn_agent_process(
            handler,  # type: ignore[arg-type]
            binary,
            *ACP_ARGS[agent],
            env=probe_env(home),
            cwd=str(home),
        ) as (connection, _):
            # The same client capabilities ``AcpRuntime`` declares.
            return await connection.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=client_capabilities(),
                client_info=Implementation(name="myrm-acp-client", version="1.0.0"),
            )


@pytest.mark.parametrize("agent", sorted(ACP_ARGS))
async def test_the_agent_still_answers_initialize_with_what_the_runtime_negotiates(agent: str, tmp_path: Path) -> None:
    init = await _initialize(agent, tmp_path)

    assert handshake_gaps(agent, init, protocol_version=PROTOCOL_VERSION) == []
