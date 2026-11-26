"""Doubles shared by the AcpRuntime tests: a PNG of a given size, a pool over the scripted ACP agent and a turn run through it."""

from __future__ import annotations

import sys
from pathlib import Path

from myrm_agent_harness.toolkits.acp.runtime.pool import RuntimePool
from myrm_agent_harness.toolkits.acp.types import PermissionMode, RuntimeConfig, RuntimeEvent, RuntimeEventType

MOCK_AGENT = Path(__file__).parents[2] / "_mock_acp_agent.py"
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def make_png(directory: Path, name: str, size: int) -> tuple[Path, bytes]:
    body = _PNG_SIGNATURE + bytes(range(256)) * ((size - len(_PNG_SIGNATURE)) // 256)
    path = directory / name
    path.write_bytes(body)
    return path, body


def make_pool(*agent_args: str, permission_mode: PermissionMode = "safe", timeout_seconds: int = 60) -> RuntimePool:
    pool = RuntimePool(max_concurrent=1)
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


async def run_turn(pool: RuntimePool, images: list[str], *, workspace: Path) -> tuple[str, list[RuntimeEvent]]:
    text: list[str] = []
    events: list[RuntimeEvent] = []
    async for event in pool.run_turn(
        "mock", "look at this", session_id="chat-1", workspace=str(workspace), images=images
    ):
        events.append(event)
        if event.type == RuntimeEventType.TEXT_DELTA:
            text.append(str(event.data.get("content")))
    return "".join(text), events
