"""Doubles shared by the AcpRuntime tests: a PNG of a given size, a pool over the scripted ACP agent, a turn run through it,
the continuity notices of its events and an in-memory session store."""

from __future__ import annotations

import sys
from pathlib import Path

from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import (
    CliSessionRecord,
    CliSessionStore,
    ContinuityStatus,
    PermissionMode,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
)

MOCK_AGENT = Path(__file__).parents[2] / "_mock_acp_agent.py"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def make_png(directory: Path, name: str, size: int) -> tuple[Path, bytes]:
    body = _PNG_SIGNATURE + bytes(range(256)) * ((size - len(_PNG_SIGNATURE)) // 256)
    path = directory / name
    path.write_bytes(body)
    return path, body


class MemorySessionStore:
    """A host's session store kept in a dict; ``saved`` lists every write, in order."""

    def __init__(self) -> None:
        self.records: dict[str, CliSessionRecord] = {}
        self.saved: list[tuple[str, CliSessionRecord]] = []

    async def load(self, session_id: str) -> CliSessionRecord | None:
        return self.records.get(session_id)

    async def save(self, session_id: str, record: CliSessionRecord) -> None:
        self.records[session_id] = record
        self.saved.append((session_id, record))


def make_pool(
    *agent_args: str,
    permission_mode: PermissionMode = "safe",
    timeout_seconds: int = 60,
    session_store: CliSessionStore | None = None,
) -> RuntimePool:
    pool = RuntimePool(max_concurrent=1, cli_session_store=session_store)
    pool.register(
        "mock",
        RuntimeConfig(
            backend_type="acp",
            command=sys.executable,
            args=[str(MOCK_AGENT), *agent_args],
            timeout_seconds=timeout_seconds,
            permission_mode=permission_mode,
        ),
    )
    return pool


async def run_turn(
    pool: RuntimePool,
    images: list[str],
    *,
    workspace: Path,
    prompt: str = "look at this",
    session_id: str = "chat-1",
    agent: str = "mock",
) -> tuple[str, list[RuntimeEvent]]:
    text: list[str] = []
    events: list[RuntimeEvent] = []
    async for event in pool.run_turn(agent, prompt, session_id=session_id, workspace=str(workspace), images=images):
        events.append(event)
        if event.type == RuntimeEventType.TEXT_DELTA:
            text.append(str(event.data.get("content")))
    return "".join(text), events


def continuity_notices(events: list[RuntimeEvent]) -> list[ContinuityStatus]:
    """The notices about the conversation (it was not continued, its folder changed) that the events carry, in order."""
    notices = (ContinuityStatus.of(e.data.get("status")) for e in events if e.type is RuntimeEventType.STATUS_UPDATE)
    return [notice for notice in notices if notice is not None]
