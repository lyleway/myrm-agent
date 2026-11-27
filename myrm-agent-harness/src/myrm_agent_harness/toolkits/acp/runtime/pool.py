"""RuntimePool — unified pool for managing arbitrary RuntimeBackend instances.

Supports ACP, app-server and CLI backends with config-driven registration and
concurrency control via a global asyncio.Semaphore plus per-backend
serialization locks. Every turn names the workspace it runs in: a backend belongs to one
(name, workspace) pair for its whole life, is created lazily on first use, and a resident
process (ACP agent, app server) that has exited is reconnected by its next ``run_turn``. A ``oneshot``
turn runs on a private backend (fresh session, closed afterwards) so it never
disturbs the shared backend's persistent session. ``release_resident`` frees the
resident processes of an idle pool while CLI backends keep their resume state. An optional
``CliSessionStore`` lets the CLI and app-server backends resume their sessions after the host restarts.


[INPUT]
- myrm_agent_harness.toolkits.acp.types::RuntimeBackend, RuntimeConfig, RuntimeEvent, CliSessionStore, PermissionBroker, delegation_session_id, images_not_sent_event, normalize_workspace (POS: ACP runtime type definitions layer.)
- myrm_agent_harness.toolkits.acp.runtime.*::AcpRuntime, CliRuntime, CodexRuntime (POS: runtime backend implementations)
- myrm_agent_harness.toolkits.acp.auth._profiles::effective_transport (POS: Authentication profile registry; the transport a known agent's contract names)
- myrm_agent_harness.core.security.redact::command_preview (POS: masked, cut command for a log line)

[OUTPUT]
- RuntimePool: unified runtime instance pool managing multiple backend types

[POS]
Runtime pool management layer. Provides multi-backend unified management, concurrency control,
and config-driven registration — the central dispatcher of the runtime system. A known agent runs over the transport its
contract names, whatever the registered config says.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import aclosing
from dataclasses import replace
from uuid import uuid4

from myrm_agent_harness.core.security.redact import command_preview
from myrm_agent_harness.toolkits.acp.auth._profiles import effective_transport
from myrm_agent_harness.toolkits.acp.types import (
    RESIDENT_BACKEND_TYPES,
    CliSessionStore,
    McpServerConfig,
    PermissionBroker,
    RuntimeBackend,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
    delegation_session_id,
    images_not_sent_event,
    normalize_workspace,
)

logger = logging.getLogger(__name__)

# (backend name, workspace): the identity of a pooled backend.
type BackendKey = tuple[str, str]


def _create_runtime(
    name: str,
    config: RuntimeConfig,
    *,
    workspace: str,
    cli_session_store: CliSessionStore | None = None,
) -> RuntimeBackend:
    """Factory: create the appropriate RuntimeBackend based on config.backend_type."""
    if config.backend_type == "acp":
        from myrm_agent_harness.toolkits.acp.runtime.acp.runtime import AcpRuntime

        return AcpRuntime(name, config, workspace=workspace)

    if config.backend_type == "cli":
        from myrm_agent_harness.toolkits.acp.runtime.cli.runtime import CliRuntime

        return CliRuntime(name, config, workspace=workspace, session_store=cli_session_store)

    if config.backend_type == "app_server":
        from myrm_agent_harness.toolkits.acp.runtime.codex.runtime import CodexRuntime

        return CodexRuntime(name, config, workspace=workspace, session_store=cli_session_store)

    msg = f"Unknown backend_type: {config.backend_type!r}"
    raise ValueError(msg)


async def _safe_close(name: str, backend: RuntimeBackend) -> None:
    """Close a backend, logging instead of raising so one failure cannot strand the others."""
    try:
        await backend.close()
    except Exception:
        logger.warning("pool_close_failed backend=%s", name, exc_info=True)


class RuntimePool:
    """Manages multiple RuntimeBackend instances with concurrency control.

    Usage::

        pool = RuntimePool(max_concurrent=4)
        pool.register("reviewer", RuntimeConfig(backend_type="acp", command="my-acp-agent"))
        response = await pool.prompt("reviewer", "Fix the bug in main.py", workspace="/work/repo")
    """

    def __init__(
        self,
        *,
        max_concurrent: int = 4,
        cli_session_store: CliSessionStore | None = None,
    ) -> None:
        self._configs: dict[str, RuntimeConfig] = {}
        self._backends: dict[BackendKey, RuntimeBackend] = {}
        # The backend running each in-flight turn, keyed by (backend name, session id): what ``cancel`` and
        # ``close_all`` reach, including the private backend of a oneshot turn.
        self._running: dict[tuple[str, str], RuntimeBackend] = {}
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._backend_locks: dict[BackendKey, asyncio.Lock] = {}
        self._cli_session_store = cli_session_store

    @property
    def available_backends(self) -> list[str]:
        """List all registered backend names."""
        return list(self._configs)

    def register(self, name: str, config: RuntimeConfig) -> None:
        """Register a backend configuration. Creates the backend lazily on first use.

        A known agent is driven over the transport its contract names, whatever ``config`` says: a config cannot make
        an agent speak a protocol it does not. Any other command keeps the transport it was configured with.
        """
        transport = effective_transport(config.command, config.backend_type)
        if transport != config.backend_type:
            logger.info(
                "pool_transport_by_contract name=%s command=%s configured=%s using=%s",
                name,
                command_preview(config.command or "", 120),
                config.backend_type,
                transport,
            )
            config = replace(config, backend_type=transport)
        self._configs[name] = config

    def get_config(self, name: str) -> RuntimeConfig | None:
        """Return the RuntimeConfig for a registered backend, or None if not found."""
        return self._configs.get(name)

    def _new_backend(
        self, name: str, workspace: str, *, cli_session_store: CliSessionStore | None = None
    ) -> RuntimeBackend:
        """Create a backend for ``name`` in ``workspace`` that the pool does not keep.

        Only the shared persistent backend is given the session store: a private oneshot backend
        is a fresh session that ends with its turn, so it has nothing to resume or to persist.

        Raises:
            KeyError: If the name is not registered.
        """
        config = self._configs.get(name)
        if config is None:
            available = ", ".join(self._configs) or "(none)"
            msg = f"Unknown backend '{name}'. Available: {available}"
            raise KeyError(msg)
        return _create_runtime(name, config, workspace=workspace, cli_session_store=cli_session_store)

    def get(self, name: str, workspace: str) -> RuntimeBackend:
        """Get or lazily create the shared (persistent) RuntimeBackend of ``name`` in ``workspace``.

        Raises:
            KeyError: If the name is not registered.
            ValueError: If ``workspace`` is not an absolute path.
        """
        key = (name, normalize_workspace(workspace))
        backend = self._backends.get(key)
        if backend is None:
            backend = self._backends[key] = self._new_backend(name, key[1], cli_session_store=self._cli_session_store)
        return backend

    async def prompt(self, name: str, task: str, *, workspace: str, mode: str = "persistent") -> str:
        """Send a task to the named backend with concurrency control.

        Collects all text_delta events and returns the concatenated response.
        """
        if mode == "oneshot":
            session_id = f"{name}-oneshot-{uuid4().hex}"
        elif mode == "persistent":
            session_id = delegation_session_id(name)
        else:
            msg = f"Invalid mode: {mode}"
            raise ValueError(msg)

        text_parts: list[str] = []
        async for event in self.run_turn(name, task, session_id=session_id, workspace=workspace, mode=mode):
            if event.type == RuntimeEventType.TEXT_DELTA:
                content = event.data.get("content")
                if isinstance(content, str):
                    text_parts.append(content)

        return "".join(text_parts)

    async def run_turn(
        self,
        name: str,
        prompt: str,
        session_id: str,
        *,
        workspace: str,
        mode: str = "persistent",
        mcp_servers: list[McpServerConfig] | None = None,
        images: list[str] | None = None,
        permissions: PermissionBroker | None = None,
    ) -> AsyncGenerator[RuntimeEvent]:
        """Stream events from a backend turn with concurrency control.

        Use this instead of ``prompt()`` when you need the full event stream.

        ``workspace`` is the absolute directory the agent starts in and is confined to; a turn never infers it.

        ``persistent`` turns share one backend per name and workspace, so the session carries over between turns.
        A ``oneshot`` turn runs on a private backend that is closed when the turn ends: it always
        starts a fresh session and never touches the persistent backend's session or process.

        ``images`` are local image files for this turn; only a backend that declares ``supports_images`` gets them.
        For the others the turn opens with an ``images_not_sent`` status update, and a host keeps the file path in its
        prompt as the fallback.

        ``permissions`` is who is asked when a ``safe`` agent wants to do more than read, for a backend that declares
        ``supports_permission_requests``; without it, or on a backend without it, the agent may only read.
        """
        if mode not in {"persistent", "oneshot"}:
            msg = f"Invalid mode: {mode}"
            raise ValueError(msg)
        workspace = normalize_workspace(workspace)

        config = self._configs.get(name)
        effective_mcp: list[McpServerConfig] | None = (
            mcp_servers if mcp_servers is not None else (list(config.mcp_servers) if config is not None else [])
        )

        oneshot = mode == "oneshot"
        async with self._semaphore:
            backend = self._new_backend(name, workspace) if oneshot else self.get(name, workspace)
            # MCP 注入前能力断言：仅向声明支持的后端注入。静默忽略会让调用方
            # 误以为 MCP 已生效，必须显式告警。
            if effective_mcp and not backend.capabilities.supports_mcp:
                logger.warning(
                    "pool_mcp_skipped backend=%s type=%s mcp_servers=%d reason=unsupported",
                    name,
                    type(backend).__name__,
                    len(effective_mcp),
                )
                effective_mcp = None
            images_withheld = 0
            if images and not backend.capabilities.supports_images:
                logger.info(
                    "pool_images_skipped backend=%s type=%s images=%d reason=unsupported",
                    name,
                    type(backend).__name__,
                    len(images),
                )
                images_withheld = len(images)
                images = None
            # Serialize turns per backend: a backend instance (e.g. CliRuntime)
            # holds mutable per-turn state (active process handle) that cannot
            # be safely shared across concurrent turns.
            backend_lock = self._backend_locks.setdefault((name, workspace), asyncio.Lock())
            async with backend_lock:
                self._running[(name, session_id)] = backend
                try:
                    async with aclosing(
                        backend.run_turn(
                            prompt,
                            session_id,
                            mcp_servers=effective_mcp or None,
                            images=images or None,
                            permissions=permissions,
                        )
                    ) as events:
                        if images_withheld:
                            yield images_not_sent_event(session_id, images_withheld)
                        async for event in events:
                            yield event
                finally:
                    if self._running.get((name, session_id)) is backend:
                        del self._running[(name, session_id)]
                    if oneshot:
                        await _safe_close(name, backend)

    async def cancel(self, name: str, session_id: str) -> None:
        """Cancel the running turn of ``session_id`` on the named backend.

        Delegates to ``backend.cancel(session_id)`` of the backend that runs that turn, which terminates the
        external process (e.g., SIGTERM → SIGKILL for CliRuntime). No-op when no such turn is running: a turn
        still waiting for its backend has nothing to stop yet, and a cancel never reaches another session's turn.
        """
        backend = self._running.get((name, session_id))
        if backend is not None:
            await backend.cancel(session_id)
            logger.info("pool_cancel backend=%s session=%s", name, session_id)

    async def release_resident(self) -> None:
        """Stop the backends that keep an OS process between turns (ACP, app server) and keep the rest.

        A CLI backend runs one process per turn, so between turns it holds nothing but the session id
        behind ``--resume``; that stays. A released backend is recreated by its next turn; an app-server
        conversation continues there only from a ``CliSessionStore`` (its threads live in the released process).
        Call it only while no turn is running on this pool.
        """
        resident = {
            key: backend
            for key, backend in self._backends.items()
            if (config := self._configs.get(key[0])) is not None and config.backend_type in RESIDENT_BACKEND_TYPES
        }
        if not resident:
            return
        for key in resident:
            del self._backends[key]
        await asyncio.gather(*[_safe_close(key[0], backend) for key, backend in resident.items()])
        logger.info("runtime_pool_released_resident backends=%s", ",".join(sorted({name for name, _ in resident})))

    async def close_all(self) -> None:
        """Close all active backend connections, including in-flight oneshot turns."""
        backends = [(name, backend) for (name, _), backend in self._backends.items()]
        shared = {id(backend) for _, backend in backends}
        backends += [(name, backend) for (name, _), backend in self._running.items() if id(backend) not in shared]
        if backends:
            await asyncio.gather(*[_safe_close(n, b) for n, b in backends])
        self._backends.clear()
        logger.info("runtime_pool_closed")
