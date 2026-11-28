"""Running ``CodexRuntime`` against the scripted stand-in of ``codex app-server`` (``_mock_app_server.py``).

The stand-in is installed as an executable named ``codex``, so the runtime treats it as the real one: the same launch
arguments, the same environment handling, the same login. ``log`` reads back everything it received.
"""

from __future__ import annotations

import json
import stat
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

from myrm_agent_harness.toolkits.acp.client_identity import ClientIdentity
from myrm_agent_harness.toolkits.acp.mcp_servers import McpServerConfig
from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    AuthMode,
    BackendType,
    CliSessionStore,
    PermissionMode,
    RuntimeConfig,
)

NAME = "codex"
_MOCK = Path(__file__).with_name("_mock_app_server.py")

type Step = dict[str, object]


class ScriptedServer:
    """One scenario of the stand-in, and the pool that runs Codex against it."""

    def __init__(self, root: Path, scenario: Mapping[str, object]) -> None:
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.scenario_path = root / "scenario.json"
        self.scenario_path.write_text(json.dumps(scenario))
        self.command = root / "codex"
        self.command.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{_MOCK}" "$@"\n')
        self.command.chmod(self.command.stat().st_mode | stat.S_IXUSR)

    def pool(
        self,
        *,
        mode: PermissionMode = "safe",
        auth_mode: AuthMode = "subscription",
        store: CliSessionStore | None = None,
        env: Mapping[str, str] | None = None,
        timeout_seconds: int = 30,
        mcp_servers: Sequence[McpServerConfig] | None = None,
        backend_type: BackendType = "cli",
        client: ClientIdentity | None = None,
    ) -> RuntimePool:
        pool = RuntimePool(max_concurrent=1, cli_session_store=store)
        pool.register(
            NAME,
            RuntimeConfig(
                backend_type=backend_type,
                command=str(self.command),
                auth_mode=auth_mode,
                env={"MOCK_APP_SERVER_SCENARIO": str(self.scenario_path), **(env or {})},
                timeout_seconds=timeout_seconds,
                permission_mode=mode,
                mcp_servers=mcp_servers or [],
                client=client or ClientIdentity(),
            ),
        )
        return pool

    def log(self) -> list[dict[str, object]]:
        path = self.root / "log.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def starts(self) -> list[dict[str, object]]:
        """What each server process was started with (argv, cwd, environment), in order."""
        return [entry["start"] for entry in self.log() if "start" in entry]  # type: ignore[misc]

    def received(self, method: str) -> list[dict[str, object]]:
        """The params of every request or notification of ``method`` the server received, in order."""
        return [
            dict(message.get("params") or {})
            for entry in self.log()
            if isinstance(message := entry.get("in"), dict) and message.get("method") == method
        ]

    def answers(self) -> list[object]:
        """What the host answered the server's requests with, in order."""
        return [entry["answer"] for entry in self.log() if "answer" in entry]


# -- the steps of a turn ---------------------------------------------------------------------------------------------


def notify(method: str, **params: object) -> Step:
    return {"notify": {"method": method, "params": {"threadId": "$thread", "turnId": "$turn", **params}}}


def say(text: str, item_id: str = "msg-1") -> Step:
    return notify("item/agentMessage/delta", itemId=item_id, delta=text)


def ask(method: str, **params: object) -> Step:
    return {"ask": {"method": method, "params": {"threadId": "$thread", "turnId": "$turn", **params}}}


def complete(status: str = "completed", error: Mapping[str, object] | None = None) -> Step:
    return {"complete": {"status": status, "error": error}}
