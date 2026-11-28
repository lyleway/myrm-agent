"""ACP protocol runtime backend.

Spawns an external ACP-compatible agent process (Gemini CLI, Qwen Code, etc.)
and communicates via the standard ACP JSON-RPC protocol over stdin/stdout.

[INPUT]
- toolkits.acp.mcp_servers::McpServerConfig, read_only_names (POS: The MCP servers a host hands a delegated agent)
- toolkits.acp.types::AcpError, AcpErrorCode, BackendCapabilities, BackendStatus, CliSessionStore, PermissionBroker, images_not_sent_event, mcp_not_attached_event (POS: ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, serving as the foundation for the entire ACP module.)
- toolkits.acp.auth._launch_args::resolve_acp_args (POS: Sole owner of the launch and permission arguments of every known agent.)
- toolkits.acp.auth._profiles::command_profile (POS: Authentication profile registry; the contract of a known agent.)
- toolkits.acp.runtime._base::KEEPALIVE_SECONDS, BaseRuntime, build_safe_env, working_status (POS: Base class for RuntimeBackend implementations.)
- toolkits.acp.runtime.acp.callback::AcpCallbackHandler, client_capabilities (POS: ACP callback handler for the AcpRuntime backend.)
- toolkits.acp.runtime.acp.capabilities::AgentCapabilities (POS: What an ACP agent said it can do in ``initialize``)
- toolkits.acp.runtime.acp.sessions::AUTH_REQUIRED_CODE, AgentSessions, OpenedSession (POS: The continuity half of AcpRuntime)
- toolkits.acp.runtime.image_blocks::ImageBlock, load_image_blocks (POS: Image input shared by CliRuntime and AcpRuntime)
- toolkits.acp.runtime.acp.stalled_agent::cancel_session, leave_transport (POS: Bounded stop of an ACP agent that has stopped reading its stdin)

[OUTPUT]
- AcpRuntime: RuntimeBackend implementation using the ACP protocol (stdin/stdout JSON-RPC).

[POS]
ACP protocol runtime backend. Every optional ACP method is used only when the agent advertised it in ``initialize``
(``AgentCapabilities``). A harness session is an ACP session of the agent, recorded so that a new agent process or host
process gets the conversation back (``sessions``); ``session/close`` is bounded, so a silent agent cannot skip the
transport teardown that terminates its process.
The host's policy answers every permission request the agent makes, so a known agent is started with its contract's
entry args (the flag that makes it ask the host included) and without the configured flags that let it skip asking
(``--yolo``, ``--approval-mode``). In ``safe`` mode a session that the agent reports in a mode that never asks is put
back in the mode that asks, or the turn is refused: the agent's own report is the only authority on what it will do.
A cancel answers the permission requests still waiting as cancelled, so the agent can end its prompt.
A refused login (JSON-RPC ``auth_required``) ends the turn as ``AUTH_FAILED`` with the agent's own words.
A silent agent (one long tool call) is reported as still working at a fixed interval, as ``CliRuntime`` does, and a
missing ACP SDK (the optional ``acp`` extra) ends the turn with a ``BACKEND_NOT_FOUND`` error that says how to install it.

The MCP servers of a turn are handed over when the session is opened; an HTTP server goes only to an agent that
advertised ``mcpCapabilities.http``, and a turn on a session that runs without a server opens with an
``mcp_not_attached`` status update naming it.

The images of a turn travel as image content blocks after the text, only to an agent that advertised
``promptCapabilities.image``; for any other agent the turn opens with an ``images_not_sent`` status
update and carries the text alone. Cancel and transport exit are bounded (``stalled_agent``), so an agent that has
stopped reading a prompt of several MiB cannot hold up a stop. A prompt failure surfaces as an error event only
once the events have been consumed to the end; when the consumer closed the turn first (the stop ended the agent) it
is logged at INFO, so closing is never replaced by that failure.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import AbstractAsyncContextManager
from typing import TYPE_CHECKING, NoReturn

from myrm_agent_harness.toolkits.acp.auth._launch_args import resolve_acp_args
from myrm_agent_harness.toolkits.acp.auth._profiles import command_profile
from myrm_agent_harness.toolkits.acp.mcp_servers import McpServerConfig, read_only_names
from myrm_agent_harness.toolkits.acp.runtime._base import (
    KEEPALIVE_SECONDS,
    BaseRuntime,
    build_safe_env,
    working_status,
)
from myrm_agent_harness.toolkits.acp.runtime.acp.callback import AcpCallbackHandler, client_capabilities
from myrm_agent_harness.toolkits.acp.runtime.acp.capabilities import AgentCapabilities
from myrm_agent_harness.toolkits.acp.runtime.acp.sessions import AUTH_REQUIRED_CODE, AgentSessions, OpenedSession
from myrm_agent_harness.toolkits.acp.runtime.acp.stalled_agent import cancel_session, leave_transport
from myrm_agent_harness.toolkits.acp.runtime.image_blocks import ImageBlock, load_image_blocks
from myrm_agent_harness.toolkits.acp.types import (
    AcpError,
    AcpErrorCode,
    BackendCapabilities,
    BackendStatus,
    CliSessionStore,
    PermissionBroker,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
    create_event,
    images_not_sent_event,
    mcp_not_attached_event,
)

if TYPE_CHECKING:
    from asyncio.subprocess import Process

    from acp import ClientSideConnection

logger = logging.getLogger(__name__)

# `session/close` is advisory. Bounded so an agent that advertises it but never answers still reaches the
# transport teardown (SDK-bounded terminate/kill) that actually stops the process.
_CLOSE_SESSION_TIMEOUT_SECONDS = 4.0


class _AcpSdkMissingError(Exception):
    """The ACP SDK (the optional ``acp`` extra of the harness) is not installed."""


def _require_acp_sdk() -> None:
    try:
        import acp  # noqa: F401
    except ModuleNotFoundError as exc:
        if exc.name != "acp":
            raise
        msg = "The ACP SDK is not installed; install it with: pip install 'myrm-agent-harness[acp]'"
        raise _AcpSdkMissingError(msg) from exc


class AcpRuntime(BaseRuntime):
    """RuntimeBackend implementation using the ACP protocol (stdin/stdout JSON-RPC).

    Spawns an external agent process and communicates via the official ACP SDK.
    Supports session reuse, cancel, and resume (across an agent restart too, given a ``CliSessionStore``).
    """

    def __init__(
        self,
        runtime_name: str,
        config: RuntimeConfig,
        *,
        workspace: str,
        session_store: CliSessionStore | None = None,
    ) -> None:
        super().__init__(runtime_name, config, backend_type="acp", workspace=workspace)
        self._conn: ClientSideConnection | None = None
        self._process: Process | None = None
        self._ctx_manager: AbstractAsyncContextManager[tuple[ClientSideConnection, Process]] | None = None
        self._handler: AcpCallbackHandler | None = None
        self._agent = AgentCapabilities()
        self._sessions = AgentSessions(runtime_name, self._workspace, session_store)

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_resume=True,
            supports_mcp=True,
            supports_streaming=True,
            supports_tools=True,
            supports_images=True,
            supports_permission_requests=True,
        )

    @property
    def is_alive(self) -> bool:
        if self._process is None:
            return False
        return self._process.returncode is None

    async def _do_run_turn(
        self,
        prompt: str,
        session_id: str,
        *,
        mcp_servers: Sequence[McpServerConfig] | None = None,
        images: list[str] | None = None,
        permissions: PermissionBroker | None = None,
    ) -> AsyncGenerator[RuntimeEvent]:
        if not self.is_alive:
            yield create_event(
                RuntimeEventType.STATUS_UPDATE,
                session_id,
                status="starting",
                message="Spawning ACP agent process",
            )
            try:
                await self._ensure_connection()
            except _AcpSdkMissingError as exc:
                logger.warning("acp_sdk_missing name=%s", self._name)
                yield create_event(
                    RuntimeEventType.ERROR,
                    session_id,
                    error=AcpError(AcpErrorCode.BACKEND_NOT_FOUND, str(exc)),
                )
                return

        handler = self._handler
        if handler is None:
            msg = "Handler not initialized"
            raise RuntimeError(msg)
        opened = await self._open_session(handler, session_id, mcp_servers)
        handler.begin_turn(permissions, session_id, read_only_names(mcp_servers))
        if opened.notice is not None:
            yield create_event(RuntimeEventType.STATUS_UPDATE, session_id, status=opened.notice)
        if opened.mcp_left_out:
            yield mcp_not_attached_event(session_id, opened.mcp_left_out)

        blocks: list[ImageBlock] = []
        if images and self._agent.image_prompts:
            blocks = await asyncio.to_thread(load_image_blocks, images)
        elif images:
            logger.info(
                "acp_runtime_images_skipped backend=%s images=%d reason=agent_without_image_prompt",
                self._name,
                len(images),
            )
            yield images_not_sent_event(session_id, len(images))
        prompt_task = asyncio.create_task(self._run_prompt(prompt, blocks, handler, opened.agent_session_id))

        drained = False
        try:
            async for event in self._consume_events(handler, session_id):
                yield event
            drained = True
        finally:
            handler.end_turn()
            if not prompt_task.done():
                prompt_task.cancel()
            try:
                await prompt_task
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                if not drained:
                    # The turn is closed or cancelled while events still flow (a stop that ended the agent): the
                    # caller is gone, and raising here would replace that close, so the base runtime would yield
                    # an error event from a generator that is being closed.
                    logger.info("acp_prompt_task_failed_after_close name=%s error=%s", self._name, exc)
                else:
                    logger.error("acp_prompt_task_failed name=%s", self._name, exc_info=True)
                    # Re-raise so the failure surfaces as an ERROR event and the
                    # BaseRuntime cleans up the process, matching CliRuntime
                    # behavior instead of degrading to a silent DONE.
                    raise

    async def _run_prompt(
        self, prompt: str, blocks: list[ImageBlock], handler: AcpCallbackHandler, agent_session_id: str
    ) -> None:
        """Run the ACP prompt (the text, then its images) and signal completion via the handler's queue."""
        from acp.schema import ImageContentBlock, TextContentBlock

        content: list[TextContentBlock | ImageContentBlock] = [TextContentBlock(type="text", text=prompt)]
        content.extend(ImageContentBlock(type="image", data=block.data, mime_type=block.media_type) for block in blocks)
        try:
            response = await self._conn.prompt(  # type: ignore[union-attr]
                prompt=content,
                session_id=agent_session_id,
            )
            handler.mark_done(response.stop_reason)
        except Exception as exc:
            handler.mark_done(f"error:{type(exc).__name__}")
            raise

    @staticmethod
    async def _consume_events(
        handler: AcpCallbackHandler,
        session_id: str,
        *,
        silence_seconds: float = KEEPALIVE_SECONDS,
    ) -> AsyncGenerator[RuntimeEvent]:
        """Consume RuntimeEvents from the handler's queue in real-time.

        An agent that says nothing for ``silence_seconds`` (one long tool call) is reported as still working, so a
        host that watches for activity does not take the quiet for a hang.
        """
        started = time.monotonic()
        while True:
            try:
                event = await asyncio.wait_for(handler.event_queue.get(), timeout=silence_seconds)
            except TimeoutError:
                yield working_status(session_id, time.monotonic() - started)
                continue
            if event is None:
                break
            yield event

    async def _do_cancel(self, session_id: str) -> None:
        agent_session_id = self._sessions.agent_session(session_id)
        if self._conn is None or agent_session_id is None:
            return
        # An agent that waits for the answer to a request does not end its prompt on ``session/cancel`` alone: the
        # requests still waiting are answered as cancelled, which ACP asks of a client that cancels.
        if self._handler is not None:
            self._handler.end_turn()
        await cancel_session(self._conn, agent_session_id, self._process, self._name)

    async def _do_resume(self, session_id: str) -> bool:
        return session_id in self._sessions and self.is_alive

    async def _do_close(self) -> None:
        await self._stop_agent()
        self._sessions.clear_memory()

    async def _stop_agent(self) -> None:
        """Stop the agent process, and with it the sessions it held; the conversations recorded stay resumable."""
        # ACP: a client MUST NOT call `session/close` unless the agent advertised sessionCapabilities.close.
        if self._conn is not None and self._agent.close_session:
            try:
                async with asyncio.timeout(_CLOSE_SESSION_TIMEOUT_SECONDS):
                    for agent_session_id in self._sessions.held():
                        await self._conn.close_session(session_id=agent_session_id)
            except TimeoutError:
                logger.warning(
                    "acp_close_session_timeout name=%s timeout_s=%.0f", self._name, _CLOSE_SESSION_TIMEOUT_SECONDS
                )
            except Exception:
                logger.debug("acp_close_session_failed name=%s", self._name, exc_info=True)

        if self._ctx_manager is not None:
            try:
                await leave_transport(self._ctx_manager, self._process, self._name)
            except Exception:
                logger.debug("acp_ctx_exit_failed name=%s", self._name, exc_info=True)

        self._conn = None
        self._process = None
        self._ctx_manager = None
        self._handler = None
        self._agent = AgentCapabilities()
        self._sessions.agent_lost()

    async def _do_get_status(self) -> BackendStatus:
        if self.is_alive:
            return "ready"
        if self._process is not None and self._process.returncode is not None:
            return "error"
        return "stopped"

    # -- Internal --

    async def _ensure_connection(self) -> None:
        """Spawn the external agent process and establish ACP connection."""
        _require_acp_sdk()
        await self._stop_agent()

        from acp import PROTOCOL_VERSION, spawn_agent_process
        from acp.schema import Implementation

        self._handler = AcpCallbackHandler(self._config, agent=self._name, workspace=self._workspace)
        safe_env = build_safe_env(self._config)

        command = self._config.command
        if command is None:
            msg = "AcpRuntime requires 'command' in RuntimeConfig"
            raise ValueError(msg)

        # The host's policy answers this agent's permission requests; a flag that makes the agent skip asking
        # (``--yolo``) would walk past it, whatever mode the host selected.
        launch = resolve_acp_args(command, self._config.args)
        if launch.dropped:
            logger.warning("acp_approval_flags_dropped name=%s flags=%s", self._name, launch.dropped)

        # AcpCallbackHandler implements the acp.Client callbacks used here
        # (permission/session/file); terminal & elicitation callbacks are never
        # invoked because the connection declares terminal=False.
        ctx = spawn_agent_process(
            self._handler,  # type: ignore[arg-type]
            command,
            *launch.args,
            env=safe_env,
            cwd=self._workspace,
        )
        self._ctx_manager = ctx
        conn, process = await ctx.__aenter__()
        self._conn = conn
        self._process = process
        self._alive = True

        client = self._config.client
        init_response = await conn.initialize(
            protocol_version=PROTOCOL_VERSION,
            client_capabilities=client_capabilities(),
            client_info=Implementation(name=client.name, title=client.title, version=client.version),
        )
        self._agent = AgentCapabilities.of(init_response)
        logger.info("acp_runtime_connected name=%s pid=%s capabilities=%s", self._name, process.pid, self._agent)

    async def _open_session(
        self, handler: AcpCallbackHandler, session_id: str, mcp_servers: Sequence[McpServerConfig] | None
    ) -> OpenedSession:
        """The ACP session of ``session_id`` (opened, or given back from the agent's storage), in a mode that asks."""
        if self._conn is None:
            msg = "Cannot open a session: connection not established"
            raise RuntimeError(msg)
        opened = await self._sessions.open(self._conn, session_id, self._agent, mcp_servers)
        await self._require_ask_mode(opened)
        return opened

    async def _require_ask_mode(self, opened: OpenedSession) -> None:
        """In ``safe`` mode, put a session that never asks back in the mode that asks, or refuse to run it.

        The launch args ask for the mode in which the agent puts its actions to the host, but the agent's own settings
        or a trust rule can still leave the session in one that acts without asking, and the agent's own report is
        the only authority on what it will do.
        """
        profile = command_profile(self._config.command)
        expected = profile.acp_ask_mode if profile is not None else None
        modes = opened.modes
        if self._config.permission_mode != "safe" or expected is None or modes is None:
            return
        if modes.current_mode_id == expected:
            return
        problem = (
            f"Agent '{self._name}' reports session mode '{modes.current_mode_id}', in which it need not ask the host"
        )
        if self._conn is None or expected not in {mode.id for mode in modes.available_modes}:
            await self._refuse_session(f"{problem}, and offers no mode '{expected}' to leave it for")
        try:
            await self._conn.set_session_mode(session_id=opened.agent_session_id, mode_id=expected)
        except Exception as exc:
            await self._refuse_session(f"{problem}, and could not be put in '{expected}': {exc}")
        logger.warning("acp_session_mode_reset name=%s from=%s to=%s", self._name, modes.current_mode_id, expected)

    async def _refuse_session(self, reason: str) -> NoReturn:
        """End the agent whose session cannot be run safely, so no later turn finds it half set up, and fail the turn."""
        await self._stop_agent()
        raise RuntimeError(reason)

    def _classify_failure(self, exc: Exception) -> AcpError | None:
        try:
            from acp import RequestError
        except ImportError:
            return None
        if isinstance(exc, RequestError) and exc.code == AUTH_REQUIRED_CODE:
            return AcpError(AcpErrorCode.AUTH_FAILED, str(exc))
        return None
