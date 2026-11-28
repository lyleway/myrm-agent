"""CLI runtime backend — one external CLI agent process per turn, streamed as RuntimeEvents.

Supports Claude Code (``claude -p --output-format stream-json``) and any CLI that prints newline-delimited JSON
events or plain text. The CLI's own session id is what carries a conversation between turns:
it is stored the moment the CLI announces it and replayed through the CLI's resume syntax (see ``claude``).

[INPUT]
- core.security.redact::command_preview (POS: masked, cut command for a log line)
- toolkits.acp.mcp_servers::McpServerConfig (POS: The MCP servers a host hands a delegated agent)
- toolkits.acp.types::AcpError, AcpErrorCode, BackendCapabilities, CliSessionRecord, CliSessionStore, ContinuityStatus, StopReason, mcp_not_attached_event (POS: ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, serving as the foundation for the entire ACP module.)
- toolkits.acp.runtime._base::KEEPALIVE_SECONDS, BaseRuntime, build_safe_env, working_status (POS: Base class for RuntimeBackend implementations)
- toolkits.acp.runtime.turn_permissions::TurnPermissions (POS: The permission broker of one turn, as the backend of that turn sees it)
- toolkits.acp.runtime.cli.permission_bridge::PermissionBridge (POS: Loopback MCP endpoint through which Claude Code puts its permission prompts to the host)
- toolkits.acp.runtime.cli.claude::is_claude, ENV_DEFAULTS and the ``with_*`` args builders (POS: Launch knowledge of CliRuntime about the one CLI it knows)
- toolkits.acp.runtime.cli.failure::classify_failure (POS: Error vocabulary of CliRuntime)
- toolkits.acp.runtime.image_blocks::load_image_blocks (POS: Image input shared by CliRuntime and AcpRuntime)
- toolkits.acp.runtime.process::ManagedProcess, ProcessLaunchError, spawn_managed_process (POS: Process ownership of every runtime that starts an agent)
- toolkits.acp.runtime.session_book::CliSessionBook (POS: Session continuity state of the runtimes that continue an agent's own conversation)
- toolkits.acp.runtime.cli.turn_parser::CliResult, TurnParser (POS: Output parsing of CliRuntime)

[OUTPUT]
- CliRuntime: RuntimeBackend that runs a CLI agent process per turn and continues its conversation across turns

[POS]
CLI runtime backend — decides how a turn is launched, resumed, retried and judged; process and parsing details
live in the sibling modules.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncGenerator, Sequence
from contextlib import AsyncExitStack, aclosing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from myrm_agent_harness.core.security.redact import command_preview
from myrm_agent_harness.toolkits.acp.auth._launch_args import resolve_cli_args
from myrm_agent_harness.toolkits.acp.mcp_servers import McpServerConfig
from myrm_agent_harness.toolkits.acp.runtime._base import (
    KEEPALIVE_SECONDS,
    BaseRuntime,
    build_safe_env,
    working_status,
)
from myrm_agent_harness.toolkits.acp.runtime.cli import claude
from myrm_agent_harness.toolkits.acp.runtime.cli.failure import classify_failure
from myrm_agent_harness.toolkits.acp.runtime.cli.turn_parser import CliResult, TurnParser
from myrm_agent_harness.toolkits.acp.runtime.image_blocks import load_image_blocks
from myrm_agent_harness.toolkits.acp.runtime.process import ManagedProcess, ProcessLaunchError, spawn_managed_process
from myrm_agent_harness.toolkits.acp.runtime.session_book import CliSessionBook
from myrm_agent_harness.toolkits.acp.runtime.turn_permissions import TurnPermissions
from myrm_agent_harness.toolkits.acp.types import (
    AcpError,
    AcpErrorCode,
    BackendCapabilities,
    BackendStatus,
    CliSessionRecord,
    CliSessionStore,
    ContinuityStatus,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
    StopReason,
    create_event,
    mcp_not_attached_event,
)

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.acp.runtime.cli.permission_bridge import PermissionBridge

logger = logging.getLogger(__name__)

_ACTIVITY_EVENTS = frozenset(
    {
        RuntimeEventType.TEXT_DELTA,
        RuntimeEventType.REASONING_DELTA,
        RuntimeEventType.TOOL_START,
        RuntimeEventType.TOOL_RESULT,
    }
)


@dataclass(slots=True)
class _Attempt:
    """What one process run turned out to be; filled in while its events stream."""

    announced: bool = False
    has_text: bool = False
    has_activity: bool = False
    error_streamed: bool = False
    returncode: int = 0
    killed: bool = False
    stderr: str = ""
    notice: str = ""
    # What the CLI said when this runtime ended the run for retrying a request the API answers 401 for.
    auth_rejection: str = ""
    result: CliResult | None = None

    def note(self, event: RuntimeEvent) -> None:
        if event.type in _ACTIVITY_EVENTS:
            self.has_activity = True
        if event.type is RuntimeEventType.TEXT_DELTA:
            self.has_text = True
        elif event.type is RuntimeEventType.ERROR:
            self.error_streamed = True

    @property
    def is_stale_resume(self) -> bool:
        """A resumed run the CLI itself rejected before it announced a session or did anything.

        That is the CLI saying it no longer knows the conversation (cleared or expired). A run this runtime
        stopped says nothing about the conversation, and one that announced a session knew it.
        """
        return not self.announced and self.returncode > 0 and not self.killed and not self.has_activity


class CliRuntime(BaseRuntime):
    """RuntimeBackend that runs a CLI process per turn and parses its NDJSON output.

    The prompt reaches the CLI on stdin (``-p``) or as the last argument. Claude Code
    is resumed from the session it announced earlier, provided the turn starts in the directory that created it;
    with a ``session_store`` that id also survives a restart of the host process. A resume the CLI rejects as
    unknown is retried once as a fresh session in the same turn, and the old id is replaced only once the fresh
    session announced itself. While the CLI prints nothing (one long tool call) the turn reports ``working`` with
    its running time at a fixed interval, so a host that watches for activity does not take the quiet for a hang.
    """

    def __init__(
        self,
        runtime_name: str,
        config: RuntimeConfig,
        *,
        workspace: str,
        session_store: CliSessionStore | None = None,
    ) -> None:
        super().__init__(runtime_name, config, backend_type="cli", workspace=workspace)
        self._process: ManagedProcess | None = None
        self._stop_requested = False
        self._sessions = CliSessionBook(runtime_name, session_store)
        # Claude is the one CLI whose command line is known: it resumes, takes images and asks the host.
        self._is_claude = claude.is_claude(config.command)

    @property
    def capabilities(self) -> BackendCapabilities:
        return BackendCapabilities(
            supports_resume=self._is_claude,
            supports_mcp=self._is_claude,
            supports_streaming=True,
            supports_tools=False,
            supports_images=self._is_claude,
            supports_permission_requests=self._is_claude,
        )

    @property
    def is_alive(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def _do_run_turn(
        self,
        prompt: str,
        session_id: str,
        *,
        mcp_servers: Sequence[McpServerConfig] | None = None,
        images: list[str] | None = None,
        permissions: TurnPermissions | None = None,
    ) -> AsyncGenerator[RuntimeEvent]:
        command = self._config.command
        if command is None:
            msg = "CliRuntime requires 'command' in RuntimeConfig"
            raise ValueError(msg)

        self._stop_requested = False
        yield create_event(RuntimeEventType.STATUS_UPDATE, session_id, status="starting")
        if mcp_servers and not self._is_claude:
            # Only Claude Code reads MCP servers from its command line; the pool withholds them from the others
            # already, so this is a caller that went around it.
            logger.warning(
                "cli_runtime_mcp_ignored backend=%s mcp_servers=%d reason=unsupported", self._name, len(mcp_servers)
            )
            yield mcp_not_attached_event(session_id, [server.name for server in mcp_servers])
            mcp_servers = None

        cwd = self._workspace
        if images and not self._is_claude:
            # Same as MCP above: the pool already withholds what a backend did not declare it takes.
            logger.warning(
                "cli_runtime_images_ignored backend=%s images=%d reason=unsupported", self._name, len(images)
            )
        env = {**(claude.ENV_DEFAULTS if self._is_claude else {}), **build_safe_env(self._config)}
        args = self._launch_args(command)
        if images and self._is_claude:
            blocks = await asyncio.to_thread(load_image_blocks, images)
            args, prompt = claude.with_image_blocks(args, prompt, blocks)

        async with AsyncExitStack() as stack:
            if mcp_servers:
                args = claude.with_mcp_servers(
                    args, stack.enter_context(claude.mcp_config_file(mcp_servers)), mcp_servers
                )
            if permissions is not None and self._asks_host():
                bridge = await stack.enter_async_context(self._permission_bridge(permissions, session_id))
                args = claude.with_permission_bridge(args, bridge.prompt_tool, bridge.config_path)
            async with aclosing(self._run_turn_attempts(args, prompt, session_id, cwd, env)) as events:
                async for event in events:
                    yield event

    def _asks_host(self) -> bool:
        """Whether this turn can put Claude's permission prompts to the host: ``safe`` is the mode that asks."""
        return self._is_claude and self._config.permission_mode == "safe"

    def _permission_bridge(self, permissions: TurnPermissions, session_id: str) -> PermissionBridge:
        # Imported here: the MCP and ASGI machinery is only loaded by a turn that is actually bridged.
        from myrm_agent_harness.toolkits.acp.runtime.cli.permission_bridge import PermissionBridge

        return PermissionBridge(permissions, agent=self._name, session_id=session_id)

    async def _run_turn_attempts(
        self,
        args: list[str],
        prompt: str,
        session_id: str,
        cwd: str,
        env: dict[str, str],
    ) -> AsyncGenerator[RuntimeEvent]:
        """Resume the session when it can be, retry once as a fresh one when the CLI rejects it, then close the turn."""
        resume_args, notice = await self._resume_args(session_id, cwd, args)
        if notice is not None:
            yield create_event(RuntimeEventType.STATUS_UPDATE, session_id, status=notice)

        attempt = _Attempt()
        try:
            async with aclosing(
                self._run_attempt(resume_args or args, prompt, session_id, cwd, env, attempt)
            ) as events:
                async for event in events:
                    yield event

            if resume_args is not None and attempt.is_stale_resume:
                logger.warning(
                    "cli_resume_stale name=%s session=%s code=%d", self._name, session_id, attempt.returncode
                )
                yield create_event(RuntimeEventType.STATUS_UPDATE, session_id, status=ContinuityStatus.SESSION_EXPIRED)
                attempt = _Attempt()
                async with aclosing(self._run_attempt(args, prompt, session_id, cwd, env, attempt)) as events:
                    async for event in events:
                        yield event
        except ProcessLaunchError as exc:
            logger.warning("cli_launch_failed name=%s command=%s", self._name, command_preview(args[0], 200))
            yield create_event(
                RuntimeEventType.ERROR,
                session_id,
                error=AcpError(AcpErrorCode.BACKEND_NOT_FOUND, str(exc)),
            )
            return

        for event in self._closing_events(attempt, session_id):
            yield event

    def _launch_args(self, command: str) -> list[str]:
        """The command line of a fresh turn: the CLI's launch and permission args, plus what Claude adds."""
        args = [command, *resolve_cli_args(command, self._config.args, self._config.permission_mode)]
        if "--output-format" in args and "stream-json" in args and "--verbose" not in args:
            args.append("--verbose")
        if self._is_claude:
            args = claude.with_partial_messages(args)
            args = claude.with_max_turns(args, self._config.max_turns)
        return args

    async def _resume_args(
        self,
        session_id: str,
        cwd: str,
        args: list[str],
    ) -> tuple[list[str] | None, ContinuityStatus | None]:
        """``args`` that continue this session's CLI conversation, and the status to tell when one cannot be.

        A conversation is resumed only from the directory that created it: Claude files sessions per project
        directory, and before v2.1.223 ``--resume`` finds an id only from there.
        """
        if not self._is_claude or claude.is_resuming(args):
            return None, None
        record = await self._sessions.recorded(session_id)
        if record is None:
            return None, None
        if record.cwd != cwd:
            logger.info(
                "cli_session_cwd_changed name=%s session=%s recorded_cwd=%s cwd=%s",
                self._name,
                session_id,
                record.cwd,
                cwd,
            )
            return None, ContinuityStatus.WORKSPACE_CHANGED
        resumed = claude.with_resume(args, record.cli_session_id)
        if resumed is None:
            logger.warning("cli_session_not_resumable name=%s session=%s", self._name, session_id)
            return None, None
        logger.info("cli_resume name=%s session=%s cli_session=%s", self._name, session_id, record.cli_session_id)
        return resumed, None

    async def _run_attempt(
        self,
        args: list[str],
        prompt: str,
        session_id: str,
        cwd: str,
        env: dict[str, str],
        attempt: _Attempt,
    ) -> AsyncGenerator[RuntimeEvent]:
        """Run ``args`` once, streaming its events and recording what the run turned out to be in ``attempt``."""
        uses_stdin = "-p" in args
        argv = args if uses_stdin else [*args, prompt]
        parser = TurnParser(session_id)
        started = time.monotonic()
        async with spawn_managed_process(argv, env=env, cwd=cwd, stdin_text=prompt if uses_stdin else None) as process:
            self._process = process
            if self._stop_requested:
                await process.terminate()
            async for line in process.lines(idle_tick=KEEPALIVE_SECONDS):
                if attempt.auth_rejection:
                    continue
                if line is None:
                    yield working_status(session_id, time.monotonic() - started)
                    continue
                for event in parser.feed(line):
                    attempt.note(event)
                    yield event
                announced = parser.take_announced()
                if announced is not None:
                    attempt.announced = True
                    await self._record_session(session_id, announced, cwd)
                if parser.auth_rejection:
                    # The CLI would keep retrying for minutes and then fail with these same words.
                    logger.warning("cli_auth_rejected name=%s session=%s", self._name, session_id)
                    attempt.auth_rejection = parser.auth_rejection
                    await process.terminate()
            attempt.returncode = await process.wait()
            attempt.killed = process.killed_by_us
            attempt.stderr = process.stderr_text
            attempt.notice = parser.notice
            attempt.result = parser.result

    async def _record_session(self, session_id: str, cli_session_id: str, cwd: str) -> None:
        """Keep the session the CLI just announced — at once, so a turn stopped right after still continues."""
        if not self._is_claude:
            return
        if not claude.is_session_id(cli_session_id):
            logger.warning("cli_session_unrecognized name=%s session=%s", self._name, session_id)
            return
        self._sessions.remember(session_id, CliSessionRecord(cli_session_id, cwd))
        logger.info("cli_session_announced name=%s session=%s cli_session=%s", self._name, session_id, cli_session_id)
        await self._sessions.persist(session_id)

    def _closing_events(self, attempt: _Attempt, session_id: str) -> list[RuntimeEvent]:
        """The events that end a turn: DONE, or the one ERROR that says why it failed."""
        error = classify_failure(
            result=attempt.result,
            returncode=attempt.returncode,
            stderr=attempt.stderr,
            killed=attempt.killed,
            notice=attempt.notice,
            auth_rejection=attempt.auth_rejection,
        )
        hit_turn_limit = attempt.result is not None and attempt.result.hit_turn_limit
        stop_reason = StopReason.MAX_TURN_REQUESTS if hit_turn_limit else StopReason.END_TURN
        done = create_event(RuntimeEventType.DONE, session_id, stop_reason=stop_reason)
        if error is None:
            return [done]
        if error.code is AcpErrorCode.PROCESS_CRASHED:
            if attempt.error_streamed:
                return []
            if attempt.has_text:
                logger.warning(
                    "cli_process_exited_nonzero name=%s code=%d stderr=%s",
                    self._name,
                    attempt.returncode,
                    attempt.stderr[:200],
                )
                return [done]
        return [create_event(RuntimeEventType.ERROR, session_id, error=error)]

    async def _do_cancel(self, session_id: str) -> None:
        # A stop can land while no process exists yet (the turn is loading its session, or between an attempt
        # and its retry); it then stops the process the moment one is spawned.
        self._stop_requested = True
        if self._process is not None:
            await self._process.terminate()

    async def _do_resume(self, session_id: str) -> bool:
        return session_id in self._sessions

    async def _do_close(self) -> None:
        await self._do_cancel("")
        self._process = None
        self._sessions.clear_memory()

    async def _do_get_status(self) -> BackendStatus:
        if self._process is None:
            return "stopped"
        if self._process.returncode is None:
            return "ready"
        return "error" if self._process.returncode != 0 else "stopped"
