"""The real Gemini and Qwen still start the way the contract says: asking the host, and refusing a missing login in words.

An ACP agent's own settings can put it in a mode that acts without asking the host, so the launch args carry the flag
that asks. Each agent is started the way ``AcpRuntime`` starts it and ``session/new`` reports the mode it is in. The
control (the settings with the entry flag alone) shows the settings do change the mode, so a green result on the
contract's args proves the flag and not a settings file that was never read. Offline: no login, no model.

Run with ``-m integration``. An agent that is not installed skips its tests, unless ``MYRM_CONTRACT_REQUIRE_CLIS`` names it.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from pathlib import Path

import pytest
from acp import PROTOCOL_VERSION, RequestError, spawn_agent_process
from acp.schema import Implementation

from myrm_agent_harness.toolkits.acp.auth._launch_args import resolve_acp_args
from myrm_agent_harness.toolkits.acp.runtime.acp.callback import AcpCallbackHandler, client_capabilities
from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import AcpErrorCode, RuntimeConfig, RuntimeEventType
from tests.toolkits.acp.cli_contract import cli_binary, probe_env

pytestmark = pytest.mark.integration

_TIMEOUT_SECONDS = 120
# The error code ACP gives a refused login (``auth_required``).
_AUTH_REQUIRED = -32000


@dataclass(frozen=True)
class _Agent:
    """What each agent needs to reach ``session/new`` offline, and the setting that moves it out of the asking mode."""

    home_dir: str
    # A fake credential, so the agent creates a session without calling the model, and what makes its settings count.
    env: dict[str, str]
    settings: dict[str, object]
    # The mode the settings alone leave the session in.
    settings_mode: str


_AGENTS = {
    "gemini": _Agent(
        home_dir=".gemini",
        # Gemini keeps a folder it does not trust in the asking mode whatever its settings say.
        env={"GEMINI_API_KEY": "fake-key", "GEMINI_CLI_TRUST_WORKSPACE": "true"},
        settings={"general": {"defaultApprovalMode": "auto_edit"}},
        settings_mode="autoEdit",
    ),
    "qwen": _Agent(
        home_dir=".qwen",
        env={"OPENAI_API_KEY": "fake-key", "OPENAI_BASE_URL": "http://127.0.0.1:9/v1", "OPENAI_MODEL": "fake-model"},
        settings={"tools": {"approvalMode": "yolo"}},
        settings_mode="yolo",
    ),
}


def _prepare(agent: str, tmp_path: Path, *, settings: bool, login: bool = True) -> tuple[Path, Path, dict[str, str]]:
    """A home (with the agent's settings and a fake login when asked), a workspace and the environment to run in."""
    spec = _AGENTS[agent]
    home = tmp_path / "home"
    workspace = tmp_path / "workspace"
    home.mkdir()
    workspace.mkdir()
    if settings:
        (home / spec.home_dir).mkdir()
        (home / spec.home_dir / "settings.json").write_text(json.dumps(spec.settings), encoding="utf-8")
    env = probe_env(home)
    if login:
        env.update(spec.env)
    return home, workspace, env


async def _session_mode(agent: str, args: list[str], tmp_path: Path, *, settings: bool) -> str | None:
    _, workspace, env = _prepare(agent, tmp_path, settings=settings)
    binary = cli_binary(agent)
    handler = AcpCallbackHandler(
        RuntimeConfig(backend_type="acp", command=binary), session_id="", agent=binary, workspace=str(workspace)
    )
    async with asyncio.timeout(_TIMEOUT_SECONDS):
        async with spawn_agent_process(
            handler,  # type: ignore[arg-type]
            binary,
            *args,
            env=env,
            cwd=str(workspace),
        ) as (connection, _):
            await connection.initialize(
                protocol_version=PROTOCOL_VERSION,
                client_capabilities=client_capabilities(),
                client_info=Implementation(name="myrm-acp-client", version="1.0.0"),
            )
            session = await connection.new_session(cwd=str(workspace), mcp_servers=[])
    return session.modes.current_mode_id if session.modes is not None else None


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_the_contract_args_start_the_agent_in_the_mode_that_asks(agent: str, tmp_path: Path) -> None:
    assert await _session_mode(agent, resolve_acp_args(agent, []).args, tmp_path, settings=False) == "default"


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_the_contract_args_win_over_a_settings_file_that_never_asks(agent: str, tmp_path: Path) -> None:
    assert await _session_mode(agent, resolve_acp_args(agent, []).args, tmp_path, settings=True) == "default"


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_without_the_flag_that_asks_the_settings_decide(agent: str, tmp_path: Path) -> None:
    """The control: the entry flag alone leaves the session in whatever the settings chose."""
    entry_flag = resolve_acp_args(agent, []).args[:1]

    assert await _session_mode(agent, entry_flag, tmp_path, settings=True) == _AGENTS[agent].settings_mode


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_the_args_an_older_cli_preset_stored_still_start_the_agent(agent: str, tmp_path: Path) -> None:
    launch = resolve_acp_args(agent, ["--output-format", "stream-json"])

    assert launch.dropped == []
    assert await _session_mode(agent, launch.args, tmp_path, settings=False) == "default"


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_a_missing_login_is_refused_with_the_error_code_of_a_refused_login(agent: str, tmp_path: Path) -> None:
    _, workspace, env = _prepare(agent, tmp_path, settings=False, login=False)
    binary = cli_binary(agent)
    handler = AcpCallbackHandler(
        RuntimeConfig(backend_type="acp", command=binary), session_id="", agent=binary, workspace=str(workspace)
    )

    with pytest.raises(RequestError) as refused:
        async with asyncio.timeout(_TIMEOUT_SECONDS):
            async with spawn_agent_process(
                handler,  # type: ignore[arg-type]
                binary,
                *resolve_acp_args(agent, []).args,
                env=env,
                cwd=str(workspace),
            ) as (connection, _):
                await connection.initialize(protocol_version=PROTOCOL_VERSION)
                await connection.new_session(cwd=str(workspace), mcp_servers=[])

    assert refused.value.code == _AUTH_REQUIRED


@pytest.mark.parametrize("agent", sorted(_AGENTS))
async def test_the_runtime_ends_a_turn_without_a_login_as_an_authentication_failure(agent: str, tmp_path: Path) -> None:
    home, workspace, _ = _prepare(agent, tmp_path, settings=False, login=False)
    pool = RuntimePool(max_concurrent=1)
    # Registered as a CLI, as every config stored before the agent moved to ACP says: the contract decides.
    pool.register(
        agent,
        RuntimeConfig(
            backend_type="cli", command=cli_binary(agent), env={"HOME": str(home)}, timeout_seconds=_TIMEOUT_SECONDS
        ),
    )
    errors = []
    try:
        async for event in pool.run_turn(agent, "hello", session_id="chat-1", workspace=str(workspace)):
            if event.type is RuntimeEventType.ERROR:
                errors.append(event.data["error"])
    finally:
        await pool.close_all()

    assert [error.code for error in errors] == [AcpErrorCode.AUTH_FAILED]  # type: ignore[attr-defined]
