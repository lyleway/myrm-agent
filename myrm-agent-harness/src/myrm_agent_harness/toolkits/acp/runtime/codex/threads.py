"""The conversations of a Codex app server: which thread a harness session is on, and how it is found again.

[INPUT]
- toolkits.acp.runtime.codex.protocol::TurnPolicy, resume_params, thread_params (POS: What the host says to Codex's app server)
- toolkits.acp.runtime.codex.rpc::JsonRpcPeer, RpcError (POS: A JSON-RPC peer over the stdio of Codex's app server)
- toolkits.acp.runtime.codex.usage::UsageMeter (POS: Token usage of a Codex thread, as the step each model request cost)
- toolkits.acp.runtime.codex.wire::as_obj, as_str (POS: Typed reads of the decoded JSON that Codex's app server sends)
- toolkits.acp.runtime.session_book::CliSessionBook (POS: Session continuity state of the runtimes that continue an agent's own conversation)
- toolkits.acp.types::CliSessionRecord, CliSessionStore, ContinuityStatus, McpServerConfig (POS: ACP runtime type definitions layer.)

[OUTPUT]
- ThreadBook: ``open`` the thread of a session, the ``meter`` of a loaded thread, what to forget when the server is lost

[POS]
The continuity half of CodexRuntime. A conversation is a thread of the server; its id is recorded per harness session
(and written through to the host's ``CliSessionStore`` when there is one), so a new server process, or a new host
process, resumes it from the rollout Codex keeps on disk. Without a store the threads are ephemeral and leave nothing in
the user's Codex history.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

from myrm_agent_harness.toolkits.acp.runtime.codex.protocol import TurnPolicy, resume_params, thread_params
from myrm_agent_harness.toolkits.acp.runtime.codex.rpc import JsonRpcPeer, RpcError
from myrm_agent_harness.toolkits.acp.runtime.codex.usage import UsageMeter
from myrm_agent_harness.toolkits.acp.runtime.codex.wire import as_obj, as_str
from myrm_agent_harness.toolkits.acp.runtime.session_book import CliSessionBook
from myrm_agent_harness.toolkits.acp.types import CliSessionRecord, CliSessionStore, ContinuityStatus, McpServerConfig

logger = logging.getLogger(__name__)

# Opening a thread reads a rollout from disk and starts the MCP servers' bookkeeping.
_THREAD_SECONDS = 30.0


class ThreadBook:
    """The threads of one runtime: the ones the running server has loaded, and the record each session keeps."""

    def __init__(self, owner: str, workspace: str, session_store: CliSessionStore | None) -> None:
        self._owner = owner
        self._workspace = workspace
        self._sessions = CliSessionBook(owner, session_store)
        self._persist = session_store is not None
        # The threads of the running server, each with the meter of its usage.
        self._loaded: dict[str, UsageMeter] = {}

    def __contains__(self, session_id: object) -> bool:
        return session_id in self._sessions

    def meter(self, thread_id: str) -> UsageMeter:
        """The usage meter of a loaded thread."""
        return self._loaded.setdefault(thread_id, UsageMeter())

    def server_lost(self) -> None:
        """The server is gone and the threads it held with it; the record of each conversation stays."""
        self._loaded.clear()

    def clear_memory(self) -> None:
        """The runtime is closed: nothing is kept in memory (a store still holds its records)."""
        self._loaded.clear()
        self._sessions.clear_memory()

    async def open(
        self, peer: JsonRpcPeer, session_id: str, mcp_servers: Sequence[McpServerConfig], policy: TurnPolicy
    ) -> tuple[str, ContinuityStatus | None]:
        """The thread of ``session_id``: the loaded one, the recorded one resumed, or a fresh one (and why it is fresh).

        Raises:
            RpcError, RpcClosedError, RpcTimeoutError: the server did not open a thread.
        """
        notice: ContinuityStatus | None = None
        record = await self._sessions.recorded(session_id)
        if record is not None and record.cwd != self._workspace:
            logger.info(
                "codex_session_cwd_changed name=%s session=%s recorded_cwd=%s cwd=%s",
                self._owner,
                session_id,
                record.cwd,
                self._workspace,
            )
            notice, record = ContinuityStatus.WORKSPACE_CHANGED, None
        if record is not None:
            if record.cli_session_id in self._loaded:
                return record.cli_session_id, None
            try:
                resumed = await peer.request(
                    "thread/resume",
                    resume_params(record.cli_session_id, self._workspace, policy, mcp_servers=mcp_servers),
                    timeout=_THREAD_SECONDS,
                )
            except RpcError:
                # The server says it no longer knows the conversation (removed, or never kept).
                logger.warning("codex_resume_stale name=%s session=%s", self._owner, session_id)
                notice = ContinuityStatus.SESSION_EXPIRED
            else:
                thread_id = as_str(as_obj(resumed.get("thread")).get("id")) or record.cli_session_id
                self._loaded[thread_id] = UsageMeter()
                logger.info("codex_resume name=%s session=%s", self._owner, session_id)
                return thread_id, None
        started = await peer.request(
            "thread/start",
            thread_params(self._workspace, policy, mcp_servers=mcp_servers, ephemeral=not self._persist),
            timeout=_THREAD_SECONDS,
        )
        thread_id = as_str(as_obj(started.get("thread")).get("id"))
        if not thread_id:
            raise RpcError(-32603, "The app server opened no thread")
        self._loaded[thread_id] = UsageMeter()
        self._sessions.remember(session_id, CliSessionRecord(thread_id, self._workspace))
        await self._sessions.persist(session_id)
        return thread_id, notice
