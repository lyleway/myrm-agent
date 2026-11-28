"""Who an ACP agent is told it is talking to: the identity the host names in the config, over the real handshake."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from myrm_agent_harness import __version__
from myrm_agent_harness.toolkits.acp.client_identity import ClientIdentity
from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import RuntimeConfig, RuntimeEventType

from ._fakes import MOCK_AGENT


def _config(**named: ClientIdentity) -> RuntimeConfig:
    return RuntimeConfig(
        backend_type="acp", command=sys.executable, args=[str(MOCK_AGENT)], timeout_seconds=60, **named
    )


async def _client_the_agent_saw(workspace: Path, config: RuntimeConfig) -> dict[str, str]:
    pool = RuntimePool(max_concurrent=1)
    pool.register("mock", config)
    try:
        text = "".join(
            [
                str(event.data["content"])
                async for event in pool.run_turn("mock", "/client", session_id="chat-1", workspace=str(workspace))
                if event.type is RuntimeEventType.TEXT_DELTA
            ]
        )
    finally:
        await pool.close_all()
    return json.loads(text.removeprefix("[client] "))


async def test_an_agent_is_told_what_the_host_named_itself(tmp_path: Path) -> None:
    host = ClientIdentity(name="acme.runner", title="Acme Runner", version="2.0.1")

    seen = await _client_the_agent_saw(tmp_path, _config(client=host))

    assert seen == {"name": "acme.runner", "title": "Acme Runner", "version": "2.0.1"}


async def test_a_host_that_names_nothing_is_the_harness_at_its_own_version(tmp_path: Path) -> None:
    seen = await _client_the_agent_saw(tmp_path, _config())

    assert seen == {"name": "myrm_agent_harness", "title": "Myrm Agent", "version": __version__}


@pytest.mark.parametrize("name", ["", " ", "acme runner", "acme/runner", "-acme", "acme\n"])
def test_a_name_an_agent_could_change_when_it_repeats_it_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="A client name is"):
        ClientIdentity(name=name)


def test_a_blank_version_is_refused() -> None:
    with pytest.raises(ValueError, match="version must not be blank"):
        ClientIdentity(version="  ")
