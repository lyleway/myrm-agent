"""The sessions of an ACP agent: which one a harness session is on, and how a conversation is given back to it.

[INPUT]
- toolkits.acp.runtime.acp.capabilities::AgentCapabilities (POS: What an ACP agent said it can do in ``initialize``)
- toolkits.acp.runtime.session_book::CliSessionBook (POS: Session continuity state of the runtimes that continue an agent's own conversation)
- toolkits.acp.types::CliSessionRecord, CliSessionStore, ContinuityStatus, McpServerConfig (POS: ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, serving as the foundation for the entire ACP module.)
- acp (POS: ACP SDK; optional extra, imported when a session is opened)

[OUTPUT]
- AgentSessions: ``open`` the ACP session of a harness session, and which sessions the running agent holds
- OpenedSession: the session a turn runs in, what the agent reports of it and why it is not the conversation recorded earlier
- AUTH_REQUIRED_CODE: the JSON-RPC code of an agent that wants a login first

[POS]
The continuity half of AcpRuntime. A conversation is a session of the agent; its id is recorded per harness session
(and written through to the host's ``CliSessionStore`` when there is one), so a new agent process, or a new host
process, gets the conversation back from the agent's own storage: ``session/resume`` when the agent advertised it,
else ``session/load`` (the history it replays reaches the handler before the turn begins and is dropped there),
else a fresh session that says so. An agent that advertised
neither has nothing to give back, and nothing is recorded for it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

from myrm_agent_harness.toolkits.acp.runtime.acp.capabilities import AgentCapabilities
from myrm_agent_harness.toolkits.acp.runtime.session_book import CliSessionBook
from myrm_agent_harness.toolkits.acp.types import CliSessionRecord, CliSessionStore, ContinuityStatus, McpServerConfig

if TYPE_CHECKING:
    from acp import ClientSideConnection
    from acp.schema import McpServerStdio, SessionModeState

logger = logging.getLogger(__name__)

# JSON-RPC error code of ACP's ``auth_required``: how Gemini and Qwen answer a session request without a login.
AUTH_REQUIRED_CODE = -32000


@dataclass(frozen=True, slots=True)
class OpenedSession:
    """The ACP session a turn runs in."""

    agent_session_id: str
    # What the agent reports of a session it has just opened; None for one the running agent already held.
    modes: SessionModeState | None = None
    # Why the conversation recorded earlier did not continue in this session.
    notice: ContinuityStatus | None = None


def acp_mcp_servers(mcp_servers: Sequence[McpServerConfig] | None) -> list[McpServerStdio] | None:
    """The MCP servers a turn brings, as the stdio servers an ACP session is opened with."""
    if not mcp_servers:
        return None
    from acp.schema import EnvVariable, McpServerStdio

    return [
        McpServerStdio(
            name=server.name,
            command=server.command,
            args=list(server.args),
            env=[EnvVariable(name=key, value=value) for key, value in sorted((server.env or {}).items())],
        )
        for server in mcp_servers
    ]


class AgentSessions:
    """The sessions of one runtime: the ones the running agent holds, and the record each conversation keeps."""

    def __init__(self, owner: str, workspace: str, session_store: CliSessionStore | None) -> None:
        self._owner = owner
        self._workspace = workspace
        self._records = CliSessionBook(owner, session_store)
        # Harness session id -> the ACP session of the running agent; an agent process holds only its own.
        self._held: dict[str, str] = {}

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._held

    def agent_session(self, session_id: str) -> str | None:
        """The ACP session id the running agent holds for ``session_id``."""
        return self._held.get(session_id)

    def held(self) -> list[str]:
        """The ACP session ids the running agent holds."""
        return list(self._held.values())

    def agent_lost(self) -> None:
        """The agent process is gone and the sessions it held with it; the record of each conversation stays."""
        self._held.clear()

    def clear_memory(self) -> None:
        """The runtime is closed: nothing is kept in memory (a store still holds its records)."""
        self._held.clear()
        self._records.clear_memory()

    async def open(
        self,
        conn: ClientSideConnection,
        session_id: str,
        agent: AgentCapabilities,
        mcp_servers: Sequence[McpServerConfig] | None,
    ) -> OpenedSession:
        """The ACP session of ``session_id``: the one the agent holds, the recorded one given back, or a fresh one.

        Raises:
            acp.RequestError: the agent refused to open any session (a refused login among them).
        """
        held = self._held.get(session_id)
        if held is not None:
            return OpenedSession(held)
        servers = acp_mcp_servers(mcp_servers)
        notice: ContinuityStatus | None = None
        record = await self._records.recorded(session_id)
        if record is not None and record.cwd != self._workspace:
            logger.info(
                "acp_session_cwd_changed name=%s session=%s recorded_cwd=%s cwd=%s",
                self._owner,
                session_id,
                record.cwd,
                self._workspace,
            )
            notice, record = ContinuityStatus.WORKSPACE_CHANGED, None
        if record is not None:
            reopened = await self._reopen(conn, record.cli_session_id, agent, servers)
            if reopened is not None:
                self._held[session_id] = record.cli_session_id
                return reopened
            notice = ContinuityStatus.SESSION_EXPIRED
        created = await conn.new_session(cwd=self._workspace, mcp_servers=servers)
        self._held[session_id] = created.session_id
        if agent.resume_session or agent.load_session:
            self._records.remember(session_id, CliSessionRecord(created.session_id, self._workspace))
            await self._records.persist(session_id)
        logger.info("acp_session_created name=%s session=%s", self._owner, session_id)
        return OpenedSession(created.session_id, created.modes, notice)

    async def _reopen(
        self,
        conn: ClientSideConnection,
        agent_session_id: str,
        agent: AgentCapabilities,
        servers: list[McpServerStdio] | None,
    ) -> OpenedSession | None:
        """The agent's own continuation of ``agent_session_id``, or None when it cannot continue it."""
        from acp import RequestError

        try:
            if agent.resume_session:
                response = await conn.resume_session(
                    session_id=agent_session_id, cwd=self._workspace, mcp_servers=servers
                )
                how = "resumed"
            elif agent.load_session:
                response = await conn.load_session(
                    session_id=agent_session_id, cwd=self._workspace, mcp_servers=servers
                )
                how = "loaded"
            else:
                logger.warning("acp_session_not_continuable name=%s reason=no_capability", self._owner)
                return None
        except RequestError as exc:
            if exc.code == AUTH_REQUIRED_CODE:
                raise
            # The agent no longer knows the conversation (removed, or kept by another version).
            logger.warning("acp_session_stale name=%s code=%s error=%s", self._owner, exc.code, exc)
            return None
        logger.info("acp_session_%s name=%s", how, self._owner)
        return OpenedSession(agent_session_id, response.modes)
