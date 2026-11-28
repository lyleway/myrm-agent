"""A frame of an agent far beyond asyncio's 64 KiB line limit reaches the host whole.

A tool result or a diff is one JSON-RPC line, and a reader that gave up at the line limit would end the connection in
the middle of a turn. The SDK's transport reads such a line in pieces and joins them; the runtime opens it with
``acp.spawn_agent_process`` and no limit of its own, so this holds the SDK, whose version range is open upward, to what
the runtime relies on. The other direction (a 5 MiB prompt to an agent that stopped reading) is held by
``test_stalled_agent.py``.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from acp import PROTOCOL_VERSION, spawn_agent_process
from acp.schema import AgentMessageChunk, TextContentBlock

from ._fakes import MOCK_AGENT

# Seventy-six times the default line limit: the stub agent echoes the prompt back as one frame of this size.
_FRAME_CHARS = 5_000_000
_ECHO_PREFIX = "[echo] "


class _CountingClient:
    """The host side of a turn that counts the text the agent sent; it answers no other request of the agent."""

    def __init__(self) -> None:
        self.chars = 0

    async def session_update(self, session_id: str, update: object, **kwargs: object) -> None:
        if isinstance(update, AgentMessageChunk) and isinstance(update.content, TextContentBlock):
            self.chars += len(update.content.text)


async def test_a_frame_far_beyond_the_line_limit_reaches_the_host_whole(tmp_path: Path) -> None:
    client = _CountingClient()

    # The runtime hands the SDK its callback handler the same way, which the SDK takes for a client by its methods.
    async with spawn_agent_process(client, sys.executable, str(MOCK_AGENT)) as (conn, _process):  # type: ignore[arg-type]
        await conn.initialize(protocol_version=PROTOCOL_VERSION)
        session = await conn.new_session(cwd=str(tmp_path), mcp_servers=[])
        turn = conn.prompt(
            prompt=[TextContentBlock(type="text", text="x" * _FRAME_CHARS)], session_id=session.session_id
        )
        await asyncio.wait_for(turn, timeout=60)

    assert client.chars == len(_ECHO_PREFIX) + _FRAME_CHARS
