"""Codex runtime backend: one resident ``codex app-server`` process, a thread per conversation, a turn per prompt.

Codex's own protocol (JSON-RPC over stdio) carries what a command line cannot: the dialogue as it streams, an approval
request for every edit and command the person is to decide on, a turn that can be interrupted, and a conversation that
resumes from the thread id the server announced. The prompt and its images travel as messages, never as arguments.

[INPUT]
- core.security.redact::command_preview (POS: masked, cut command for a log line)
- toolkits.acp.auth._launch_args::resolve_app_server_args (POS: Launch args and permission flags per agent)
- toolkits.acp.runtime._base::KEEPALIVE_SECONDS, BaseRuntime, build_safe_env, working_status (POS: Base class for RuntimeBackend implementations)
- toolkits.acp.runtime.codex.approvals::ApprovalResponder (POS: Answers the requests of Codex's app server with what the person decided)
- toolkits.acp.runtime.codex.connection::AppServerConnection, AppServerStartError, server_environment (POS: One running Codex app-server process, started and greeted)
- toolkits.acp.runtime.codex.failure::classify_turn_error (POS: Failure classification of a Codex turn)
- toolkits.acp.runtime.codex.items::ItemBook (POS: Items that more than one reader needs)
- toolkits.acp.runtime.codex.protocol::TurnPolicy, WorkspaceWriteSettings, config_read_params, turn_params, turn_policy (POS: What the host says to Codex's app server)
- toolkits.acp.runtime.codex.rpc::JsonRpcPeer, RpcClosedError, RpcError, RpcTimeoutError (POS: A JSON-RPC peer over the stdio of Codex's app server)
- toolkits.acp.runtime.codex.threads::ThreadBook (POS: The conversations of a Codex app server)
- toolkits.acp.runtime.codex.turn_events::TurnTranslator (POS: The events of one Codex turn)
- toolkits.acp.runtime.codex.wire::as_obj, as_str (POS: Typed reads of the decoded JSON that Codex's app server sends)
- toolkits.acp.runtime.event_text::condense_failure_text (POS: Text shaping of the events every runtime emits)
- toolkits.acp.runtime.process::ProcessLaunchError (POS: Process ownership of every runtime that starts an agent)
- toolkits.acp.runtime.turn_permissions::TurnPermissions (POS: The permission broker of one turn, as the backend of that turn sees it)
- toolkits.acp.types::AcpError, AcpErrorCode, BackendCapabilities, CliSessionStore, StopReason (POS: ACP runtime type definitions layer.)

[OUTPUT]
- CodexRuntime: RuntimeBackend that runs Codex conversations over its app server

[POS]
Decides how a turn is run: when the server is started (on the first turn, and again after it was lost), under which
approval policy and sandbox, and how a turn ends (done, refused login, stopped, failed, or a server that went away).
The wire format lives in ``protocol``, ``rpc`` and ``turn_events``; the permission decisions in ``approvals``; which
thread a turn belongs to in ``threads``.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import aclosing
from dataclasses import dataclass, field

from myrm_agent_harness.core.security.redact import command_preview
from myrm_agent_harness.toolkits.acp.auth._launch_args import resolve_app_server_args
from myrm_agent_harness.toolkits.acp.runtime._base import (
    KEEPALIVE_SECONDS,
    BaseRuntime,
    working_status,
)
from myrm_agent_harness.toolkits.acp.runtime.codex.approvals import ApprovalResponder
from myrm_agent_harness.toolkits.acp.runtime.codex.connection import (
    AppServerConnection,
    AppServerStartError,
    server_environment,
)
from myrm_agent_harness.toolkits.acp.runtime.codex.failure import classify_turn_error
from myrm_agent_harness.toolkits.acp.runtime.codex.items import ItemBook
from myrm_agent_harness.toolkits.acp.runtime.codex.protocol import (
    TurnPolicy,
    WorkspaceWriteSettings,
    config_read_params,
    turn_params,
    turn_policy,
)
from myrm_agent_harness.toolkits.acp.runtime.codex.rpc import JsonRpcPeer, RpcClosedError, RpcError, RpcTimeoutError
from myrm_agent_harness.toolkits.acp.runtime.codex.threads import ThreadBook
from myrm_agent_harness.toolkits.acp.runtime.codex.turn_events import TurnTranslator
from myrm_agent_harness.toolkits.acp.runtime.codex.wire import as_obj, as_str
from myrm_agent_harness.toolkits.acp.runtime.event_text import condense_failure_text
from myrm_agent_harness.toolkits.acp.runtime.process import ProcessLaunchError
from myrm_agent_harness.toolkits.acp.runtime.turn_permissions import TurnPermissions
from myrm_agent_harness.toolkits.acp.types import (
    AcpError,
    AcpErrorCode,
    BackendCapabilities,
    BackendStatus,
    CliSessionStore,
    McpServerConfig,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
    StopReason,
    create_event,
)

logger = logging.getLogger(__name__)

# Starting a turn only queues it; reading the user's config and opening a thread take longer.
_CONFIG_READ_SECONDS = 30.0
_TURN_START_SECONDS = 30.0
_INTERRUPT_SECONDS = 5.0
# A turn that was told to stop and still runs after this long is stopped by stopping the server.
_STOP_GRACE_SECONDS = 10.0

_RPC_FAILURES = (RpcError, RpcClosedError, RpcTimeoutError)


@dataclass(slots=True)
class _RunningTurn:
    """The turn this runtime is serving: what a stop needs to address it, and whether it already asked."""

    thread_id: str
    turn_id: str
    interrupting: bool = False
    # Set when the turn's stream is over, whichever way it ended.
    ended: asyncio.Event = field(default_factory=asyncio.Event)


def _event(session_id: str, error: AcpError) -> RuntimeEvent:
    return create_event(RuntimeEventType.ERROR, session_id, error=error)


class CodexRuntime(BaseRuntime):
    """RuntimeBackend over one resident ``codex app-server``: a thread per harness session, a turn per prompt.

    The server starts with the first turn and is started again when it was lost. A conversation is continued from the
    thread id the server announced, resumed from its rollout when the server does not hold it (a new process, or a
    host restart with a ``session_store``) and replaced by a fresh thread, with a status saying so, when the server no
    longer knows it. Without a ``session_store`` threads are ephemeral: nothing is left in the user's Codex history.
    A turn is stopped by interrupting it on the server, and only if that is not obeyed by stopping the server.
    """

    def __init__(
        self,
        runtime_name: str,
        config: RuntimeConfig,
        *,
        workspace: str,
        session_store: CliSessionStore | None = None,
    ) -> None:
        super().__init__(runtime_name, config, backend_type="app_server", workspace=workspace)
        self._threads = ThreadBook(runtime_name, workspace, session_store)
        self._items = ItemBook()
        self._approvals = ApprovalResponder(runtime_name, self._items)
        self._connection: AppServerConnection | None = None
        # The user's own settings of the sandbox an ``allow_all`` turn runs in, read from the server that was started.
        self._workspace_write = WorkspaceWriteSettings()
        self._turn: _RunningTurn | None = None
        self._stop_requested = False

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_resume=True,
            supports_mcp=True,
            supports_streaming=True,
            supports_images=True,
            supports_permission_requests=True,
        )

    @property
    def is_alive(self) -> bool:
        return self._connection is not None and self._connection.alive

    # -- the turn ---------------------------------------------------------------------------------------------

    async def _do_run_turn(
        self,
        prompt: str,
        session_id: str,
        *,
        mcp_servers: list[McpServerConfig] | None = None,
        images: list[str] | None = None,
        permissions: TurnPermissions | None = None,
    ) -> AsyncGenerator[RuntimeEvent]:
        self._stop_requested = False
        yield create_event(RuntimeEventType.STATUS_UPDATE, session_id, status="starting")

        connection: AppServerConnection | None = None
        try:
            connection = await self._ensure_connection()
            policy = turn_policy(
                self._config.permission_mode, can_ask=permissions is not None, workspace_write=self._workspace_write
            )
            thread_id, notice = await self._threads.open(connection.peer, session_id, mcp_servers or (), policy)
        except ProcessLaunchError as exc:
            logger.warning("codex_launch_failed name=%s", self._name)
            yield _event(session_id, AcpError(AcpErrorCode.BACKEND_NOT_FOUND, str(exc)))
            return
        except AppServerStartError as exc:
            yield _event(session_id, AcpError(AcpErrorCode.PROCESS_CRASHED, str(exc), retryable=True))
            return
        except _RPC_FAILURES as exc:
            yield _event(session_id, await self._rpc_failure(connection, exc))
            return

        if notice is not None:
            yield create_event(RuntimeEventType.STATUS_UPDATE, session_id, status=notice)
        if self._stop_requested:
            yield _event(session_id, _stopped())
            return
        async with aclosing(
            self._stream_turn(connection, session_id, thread_id, prompt, images or (), policy, permissions)
        ) as events:
            async for event in events:
                yield event

    async def _stream_turn(
        self,
        connection: AppServerConnection,
        session_id: str,
        thread_id: str,
        prompt: str,
        images: Sequence[str],
        policy: TurnPolicy,
        permissions: TurnPermissions | None,
    ) -> AsyncGenerator[RuntimeEvent]:
        """Start a turn on ``thread_id``, stream it until the server reports its end, and close it with DONE or an ERROR."""
        peer = connection.peer
        peer.discard_notifications()
        self._items.clear()
        self._approvals.begin_turn(session_id, permissions, self._config.permission_mode)
        running: _RunningTurn | None = None
        translator: TurnTranslator | None = None
        # Whether a turn may exist that this stream knows nothing about: a request cut short is neither answered nor refused.
        unresolved = True
        try:
            try:
                started = await peer.request(
                    "turn/start", turn_params(thread_id, prompt, images, policy), timeout=_TURN_START_SECONDS
                )
            except _RPC_FAILURES as exc:
                unresolved = False
                yield _event(session_id, await self._rpc_failure(connection, exc))
                return
            unresolved = False
            turn_id = as_str(as_obj(started.get("turn")).get("id"))
            translator = TurnTranslator(session_id, thread_id, turn_id, self._threads.meter(thread_id))
            running = self._turn = _RunningTurn(thread_id, turn_id)
            if self._stop_requested:
                await self._interrupt(running)

            began = time.monotonic()
            async with aclosing(peer.notifications(KEEPALIVE_SECONDS)) as feed:
                async for notification in feed:
                    if notification is None:
                        yield working_status(session_id, time.monotonic() - began)
                        continue
                    for event in translator.feed(notification):
                        yield event
                    if translator.auth_rejection:
                        # The server would retry the refused login for minutes and then fail with these same words.
                        logger.warning("codex_auth_rejected name=%s session=%s", self._name, session_id)
                        await self._interrupt(running)
                    if translator.outcome is not None:
                        break
            yield await self._closing_event(connection, session_id, translator)
        finally:
            self._turn = None
            self._approvals.end_turn()
            if unresolved:
                await asyncio.shield(self._drop_connection(graceful=False))
            elif running is not None and translator is not None and translator.outcome is None:
                await asyncio.shield(self._abandon(connection, running))
            if running is not None:
                running.ended.set()

    async def _closing_event(
        self, connection: AppServerConnection, session_id: str, translator: TurnTranslator
    ) -> RuntimeEvent:
        """The event that ends a turn: DONE, or the one ERROR that says why it did not complete."""
        outcome = translator.outcome
        if outcome is None:
            # The server's output ended mid-turn: it was stopped (a stop that did not take) or it died.
            error = (
                _stopped()
                if self._stop_requested
                else AcpError(AcpErrorCode.PROCESS_CRASHED, await connection.exit_reason(), retryable=True)
            )
            await self._drop_connection(graceful=False)
            return _event(session_id, error)
        if outcome.status == "completed":
            return create_event(RuntimeEventType.DONE, session_id, stop_reason=StopReason.END_TURN)
        if translator.auth_rejection:
            return _event(session_id, AcpError(AcpErrorCode.AUTH_FAILED, translator.auth_rejection))
        if outcome.status == "interrupted":
            return _event(session_id, _stopped())
        return _event(session_id, classify_turn_error(outcome.error or {}))

    async def _rpc_failure(self, connection: AppServerConnection | None, exc: Exception) -> AcpError:
        """The error of a request the server did not carry out; a server that is gone or silent is dropped."""
        if isinstance(exc, RpcError):
            return AcpError(AcpErrorCode.UNKNOWN, condense_failure_text(exc.message) or "The request was refused")
        reason = "The Codex app server stopped answering"
        if isinstance(exc, RpcClosedError) and connection is not None:
            reason = await connection.exit_reason()
        await self._drop_connection(graceful=False)
        return AcpError(AcpErrorCode.PROCESS_CRASHED, reason, retryable=True)

    # -- the server and its threads ---------------------------------------------------------------------------

    async def _ensure_connection(self) -> AppServerConnection:
        if self._connection is not None and self._connection.alive:
            return self._connection
        await self._drop_connection(graceful=False)
        command = self._config.command
        if command is None:
            msg = "CodexRuntime requires 'command' in RuntimeConfig"
            raise ValueError(msg)
        launch = resolve_app_server_args(command, self._config.args, self._config.auth_mode)
        if launch.dropped:
            logger.warning(
                "codex_launch_args_dropped name=%s args=%s", self._name, command_preview(" ".join(launch.dropped), 200)
            )
        env, api_key = server_environment(self._name, self._config, command)
        self._connection = await AppServerConnection.open(
            [command, *launch.args],
            env=env,
            cwd=self._workspace,
            on_server_request=self._approvals.handle,
            observe=self._items.observe,
            api_key=api_key,
        )
        self._workspace_write = await self._read_workspace_write(self._connection.peer)
        logger.info("codex_app_server_started name=%s", self._name)
        return self._connection

    async def _read_workspace_write(self, peer: JsonRpcPeer) -> WorkspaceWriteSettings:
        """The user's settings of the workspace-write sandbox (only an ``allow_all`` turn runs in it)."""
        if self._config.permission_mode != "allow_all":
            return WorkspaceWriteSettings()
        try:
            response = await peer.request(
                "config/read", config_read_params(self._workspace), timeout=_CONFIG_READ_SECONDS
            )
        except _RPC_FAILURES:
            logger.warning("codex_config_read_failed name=%s", self._name, exc_info=True)
            return WorkspaceWriteSettings()
        return WorkspaceWriteSettings.of(as_obj(response.get("config")))

    async def _drop_connection(self, *, graceful: bool) -> None:
        connection, self._connection = self._connection, None
        self._threads.server_lost()
        self._items.clear()
        if connection is not None:
            await connection.close(graceful=graceful)

    # -- stopping ---------------------------------------------------------------------------------------------

    async def _interrupt(self, turn: _RunningTurn) -> None:
        """Ask the server to end ``turn`` (once); the server answers with the turn's end, which the stream reads."""
        connection = self._connection
        if connection is None or turn.interrupting:
            return
        turn.interrupting = True
        try:
            await connection.peer.request(
                "turn/interrupt",
                {"threadId": turn.thread_id, "turnId": turn.turn_id},
                timeout=_INTERRUPT_SECONDS,
            )
        except _RPC_FAILURES:
            logger.info("codex_interrupt_not_taken name=%s turn=%s", self._name, turn.turn_id, exc_info=True)

    async def _abandon(self, connection: AppServerConnection, turn: _RunningTurn) -> None:
        """End a turn nobody reads any more (the stream was closed, timed out or failed), and the server with it if it will not."""
        await self._interrupt(turn)
        try:
            async with (
                asyncio.timeout(_STOP_GRACE_SECONDS),
                aclosing(connection.peer.notifications(_STOP_GRACE_SECONDS)) as feed,
            ):
                async for notification in feed:
                    if (
                        notification is not None
                        and notification.method == "turn/completed"
                        and as_str(as_obj(notification.params.get("turn")).get("id")) == turn.turn_id
                    ):
                        return
        except TimeoutError:
            pass
        logger.warning("codex_turn_abandoned name=%s turn=%s", self._name, turn.turn_id)
        await self._drop_connection(graceful=False)

    async def _do_cancel(self, session_id: str) -> None:
        # A stop can land before the turn exists (the server is starting, or the turn is being queued); the turn then
        # stops itself the moment it exists.
        self._stop_requested = True
        self._approvals.end_turn()
        turn = self._turn
        if turn is None:
            return
        await self._interrupt(turn)
        try:
            async with asyncio.timeout(_STOP_GRACE_SECONDS):
                await turn.ended.wait()
        except TimeoutError:
            logger.warning("codex_interrupt_ignored name=%s turn=%s", self._name, turn.turn_id)
            await self._drop_connection(graceful=False)

    async def _do_resume(self, session_id: str) -> bool:
        return session_id in self._threads

    async def _do_close(self) -> None:
        self._stop_requested = True
        self._approvals.end_turn()
        await self._drop_connection(graceful=True)
        self._threads.clear_memory()

    async def _do_get_status(self) -> BackendStatus:
        if self.is_alive:
            return "ready"
        process = self._connection.process if self._connection is not None else None
        return "error" if process is not None and process.returncode not in (None, 0) else "stopped"


def _stopped() -> AcpError:
    return AcpError(AcpErrorCode.CANCELLED, "The turn was stopped before it finished")
