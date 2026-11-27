"""A real Gemini or Qwen, pointed at a scripted model and registered in a pool: what the real-agent ACP tests share."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import CliSessionStore, PermissionMode, RuntimeConfig
from tests.toolkits.acp._fake_model_apis import FakeGeminiApi, FakeOpenAiChatApi, ScriptedModelApi
from tests.toolkits.acp.cli_contract import cli_binary

TURN_SECONDS = 90


@dataclass(frozen=True)
class ScriptedAgent:
    """How to point one agent at its scripted model."""

    api: type[ScriptedModelApi]
    env: Callable[[str], dict[str, str]]


SCRIPTED_AGENTS = {
    "gemini": ScriptedAgent(
        api=FakeGeminiApi,
        env=lambda url: {
            "GEMINI_API_KEY": "fake-key",
            "GOOGLE_GEMINI_BASE_URL": url,
            "GEMINI_MODEL": "gemini-2.5-flash",
        },
    ),
    "qwen": ScriptedAgent(
        api=FakeOpenAiChatApi,
        env=lambda url: {"OPENAI_API_KEY": "fake-key", "OPENAI_BASE_URL": f"{url}/v1", "OPENAI_MODEL": "fake-model"},
    ),
}


def scripted_pool(
    name: str,
    api: ScriptedModelApi,
    home: Path,
    mode: PermissionMode = "safe",
    *,
    session_store: CliSessionStore | None = None,
    account_home: Path | None = None,
) -> RuntimePool:
    """A pool with the agent registered the way every config stored before the move to ACP says: as a CLI.

    ``account_home`` runs it as the account whose home that is; ``home`` stays what the process calls its own.
    """
    pool = RuntimePool(max_concurrent=1, cli_session_store=session_store)
    pool.register(
        name,
        RuntimeConfig(
            backend_type="cli",
            command=cli_binary(name),
            auth_mode="api_key",
            env={**SCRIPTED_AGENTS[name].env(api.base_url), "HOME": str(home)},
            timeout_seconds=TURN_SECONDS,
            permission_mode=mode,
            account_home=None if account_home is None else str(account_home),
        ),
    )
    return pool
