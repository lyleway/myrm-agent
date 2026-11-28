"""The read-only access to a user's memory that a delegated external agent is handed.

[INPUT]
- app.config.settings::settings (POS: The port this server listens on)
- myrm_agent_harness.toolkits.acp.mcp_servers::HttpMcpServer (POS: The MCP servers a host hands a delegated agent)
- myrm_agent_harness.toolkits.acp.types::RuntimeConfig (POS: What a pooled external agent is configured with) [TYPE_CHECKING]

[OUTPUT]
- MEMORY_SERVER_NAME: the name the external agent sees the memory server under
- MemoryGrant: what one credential reads (a Myrm agent's memory) for which external agent, and whether that agent has used it
- MemoryGrants, get_memory_grants: issue, check and revoke credentials
- memory_server_for: the MCP server entry that gives one external agent the memory of a Myrm agent
- memory_connected: whether an external agent has connected to the memory it was given

[POS]
A delegated agent that shares the user's memory connects back to this server's own ``/mcp`` endpoint. Each pool and
external agent gets a credential of its own: it is minted when the pool is built, is valid exactly as long as the pool
(it is revoked wherever the pool is closed, and goes with a pool that is dropped), and reads one Myrm agent's memory,
never writes. No expiry clock runs on it: the agent's process holds the credential in its config for as long as its
session lives, and a clock would cut a long session off from its memory silently.
Only a digest of a credential is kept, so what this module holds cannot be replayed.
"""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import weakref
from typing import TYPE_CHECKING

from myrm_agent_harness.toolkits.acp.mcp_servers import HttpMcpServer

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.acp.types import RuntimeConfig

MEMORY_SERVER_NAME = "myrm-memory"

# An agent connects to its servers right after it opens a session (Qwen Code a moment after ``session/new`` has
# returned), so a turn that ends first waits this long before its agent counts as not connected.
_CONNECT_GRACE_SECONDS = 2.0

_BEARER_PREFIX = "Bearer "


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class MemoryGrant:
    """One credential: whose memory it reads, which external agent holds it, and whether that agent has used it."""

    __slots__ = ("_connected", "agent_id", "external_agent")

    def __init__(self, agent_id: str, external_agent: str) -> None:
        self.agent_id = agent_id
        self.external_agent = external_agent
        self._connected = asyncio.Event()

    def mark_connected(self) -> None:
        self._connected.set()

    async def wait_connected(self, timeout: float) -> bool:
        """Whether the agent has used the credential, waiting up to ``timeout`` for it to."""
        try:
            async with asyncio.timeout(timeout):
                await self._connected.wait()
        except TimeoutError:
            return False
        return True


class MemoryGrants:
    """The credentials in force, each tied to the pool it was issued to."""

    def __init__(self) -> None:
        self._grants: dict[str, MemoryGrant] = {}
        self._issued: dict[int, list[str]] = {}

    def issue(self, owner: object, *, agent_id: str, external_agent: str) -> str:
        """Mint a credential reading the memory of ``agent_id`` for ``external_agent``, valid as long as ``owner`` is."""
        token = f"myrm_dlg_{secrets.token_urlsafe(32)}"
        digest = _digest(token)
        key = id(owner)
        if key not in self._issued:
            weakref.finalize(owner, self._drop, key)
        self._issued.setdefault(key, []).append(digest)
        self._grants[digest] = MemoryGrant(agent_id, external_agent)
        return token

    def resolve(self, token: str) -> MemoryGrant | None:
        """The grant of a request's credential, counted as the agent connecting; ``None`` for one not in force."""
        grant = self.peek(token)
        if grant is not None:
            grant.mark_connected()
        return grant

    def peek(self, token: str) -> MemoryGrant | None:
        """The grant of a credential, without counting a connection."""
        return self._grants.get(_digest(token))

    def revoke(self, owner: object) -> None:
        """Invalidate every credential issued to ``owner``."""
        self._drop(id(owner))

    def _drop(self, key: int) -> None:
        for digest in self._issued.pop(key, ()):
            self._grants.pop(digest, None)


_grants = MemoryGrants()


def get_memory_grants() -> MemoryGrants:
    """The process-wide credential registry."""
    return _grants


def memory_server_for(owner: object, *, agent_id: str, external_agent: str) -> HttpMcpServer:
    """The server entry handing ``external_agent`` the memory of ``agent_id``: this server's own ``/mcp``, read only."""
    from app.config.settings import settings

    token = _grants.issue(owner, agent_id=agent_id, external_agent=external_agent)
    return HttpMcpServer(
        name=MEMORY_SERVER_NAME,
        # The endpoint is mounted at ``/mcp``; the form without the slash only redirects to this one.
        url=f"http://127.0.0.1:{settings.port}/mcp/",
        headers={"Authorization": f"{_BEARER_PREFIX}{token}"},
        read_only=True,
    )


async def memory_connected(config: RuntimeConfig | None) -> bool | None:
    """Whether the agent of ``config`` has connected to its memory: ``None`` when it was given none.

    A credential that is no longer in force counts as not connected, since the agent can no longer use it.
    """
    server = next(
        (
            entry
            for entry in (config.mcp_servers if config is not None else ())
            if isinstance(entry, HttpMcpServer) and entry.name == MEMORY_SERVER_NAME
        ),
        None,
    )
    if server is None:
        return None
    token = server.headers.get("Authorization", "").removeprefix(_BEARER_PREFIX)
    grant = _grants.peek(token)
    return grant is not None and await grant.wait_connected(_CONNECT_GRACE_SECONDS)
