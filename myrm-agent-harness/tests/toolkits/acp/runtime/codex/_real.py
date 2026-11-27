"""Running a real ``codex app-server`` against a scripted model the way the server runs it: through a ``RuntimePool``.

Codex finds its model API in its own ``config.toml`` (under an isolated ``CODEX_HOME``), so no login, no network and no
billed model are involved. The agent is registered as a CLI, the way every config stored before the move to the app
server says, which the pool then runs over the transport the agent's contract names.
"""

from __future__ import annotations

import sys
from pathlib import Path

from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    AcpError,
    CliSessionStore,
    McpServerConfig,
    PermissionBroker,
    PermissionMode,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
)
from tests.toolkits.acp.cli_contract import cli_binary

NAME = "codex"
PROMPT = "do the scripted work"
STUB_KEY = "stub-key"
TURN_SECONDS = 90

_RETRY_OFF = "request_max_retries = 0\nstream_max_retries = 0\n"


def codex_config_toml(base_url: str, *, retries: int | None = 0, builtin: bool = False) -> str:
    """The ``config.toml`` that makes Codex's only model API ``base_url``.

    ``retries`` is the provider's request and stream retry count (``None``: the CLI's defaults). ``builtin`` keeps
    Codex's own OpenAI provider and only moves its endpoint, so a login is what authenticates the requests.
    """
    if builtin:
        return f'openai_base_url = "{base_url}/v1"\nmodel = "stub-model"\n'
    retry_settings = _RETRY_OFF if retries == 0 else ""
    return (
        'model_provider = "stub"\nmodel = "stub-model"\n\n'
        '[model_providers.stub]\nname = "stub"\n'
        f'base_url = "{base_url}/v1"\nenv_key = "STUB_KEY"\nwire_api = "responses"\n{retry_settings}'
    )


def write_codex_home(root: Path, base_url: str, *, retries: int | None = 0, builtin: bool = False) -> dict[str, str]:
    """An isolated home whose only model API is ``base_url``; returns the environment that makes Codex use it."""
    home = root / "home"
    codex_home = home / ".codex"
    codex_home.mkdir(parents=True)
    (codex_home / "config.toml").write_text(codex_config_toml(base_url, retries=retries, builtin=builtin))
    return {"HOME": str(home), "CODEX_HOME": str(codex_home), "STUB_KEY": STUB_KEY}


def codex_pool(
    env: dict[str, str],
    *,
    mode: PermissionMode = "safe",
    store: CliSessionStore | None = None,
    mcp_servers: list[McpServerConfig] | None = None,
    timeout_seconds: int = TURN_SECONDS,
) -> RuntimePool:
    pool = RuntimePool(max_concurrent=1, cli_session_store=store)
    pool.register(
        NAME,
        RuntimeConfig(
            backend_type="cli",
            command=cli_binary("codex"),
            auth_mode="api_key",
            env=env,
            timeout_seconds=timeout_seconds,
            permission_mode=mode,
            mcp_servers=mcp_servers or [],
        ),
    )
    return pool


def echo_mcp_server() -> McpServerConfig:
    return McpServerConfig(
        name="echo",
        command=sys.executable,
        args=[str(Path(__file__).parents[2] / "_echo_mcp_server.py")],
    )


async def run_turn(
    pool: RuntimePool,
    workspace: Path,
    *,
    prompt: str = PROMPT,
    session_id: str = "chat-1",
    broker: PermissionBroker | None = None,
    images: list[str] | None = None,
) -> list[RuntimeEvent]:
    return [
        event
        async for event in pool.run_turn(
            NAME, prompt, session_id, workspace=str(workspace), permissions=broker, images=images
        )
    ]


def types_of(events: list[RuntimeEvent]) -> list[RuntimeEventType]:
    return [event.type for event in events]


def text_of(events: list[RuntimeEvent]) -> str:
    return "".join(str(e.data["content"]) for e in events if e.type is RuntimeEventType.TEXT_DELTA)


def errors_of(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
    return [event for event in events if event.type is RuntimeEventType.ERROR]


def failure_of(event: RuntimeEvent) -> AcpError:
    """The failure an error event carries."""
    failure = event.data["error"]
    assert isinstance(failure, AcpError)
    return failure


def failed_tool_results(events: list[RuntimeEvent]) -> list[RuntimeEvent]:
    return [e for e in events if e.type is RuntimeEventType.TOOL_RESULT and e.data["is_error"]]


def ended_cleanly(events: list[RuntimeEvent]) -> bool:
    return not errors_of(events) and types_of(events)[-1] is RuntimeEventType.DONE
