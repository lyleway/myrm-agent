"""One running Codex app-server process, started and greeted: the connection every turn of a runtime rides on.

[INPUT]
- toolkits.acp.auth._profiles::command_profile (POS: Single source of truth for per-backend CLI auth and launch behaviour)
- toolkits.acp.runtime._base::build_safe_env (POS: Base class for RuntimeBackend implementations)
- toolkits.acp.runtime.codex.protocol::MINIMUM_SERVER_VERSION, initialize_params, login_params, server_version (POS: What the host says to Codex's app server)
- toolkits.acp.runtime.codex.rpc::JsonRpcPeer, NotificationObserver, RpcClosedError, RpcError, RpcTimeoutError, ServerRequestHandler (POS: A JSON-RPC peer over the stdio of Codex's app server)
- toolkits.acp.runtime.event_text::condense_failure_text (POS: Text shaping of the events every runtime emits)
- toolkits.acp.runtime.codex.wire::Json, as_str (POS: Typed reads of the decoded JSON that Codex's app server sends)
- toolkits.acp.runtime.process::ManagedProcess, spawn_managed_process (POS: Process ownership of every runtime that starts an agent)
- toolkits.acp.types::RuntimeConfig (POS: ACP runtime type definitions layer.)

[OUTPUT]
- AppServerConnection: the process with its peer and the ``version`` it said it is, ``open`` it, ``close`` it, ask whether it is ``alive``
- AppServerStartError: the process could not be started, did not complete the handshake, or is older than the host speaks to
- server_environment: the environment a server is started with, and the API key it is logged in with

[POS]
The lifecycle half of CodexRuntime: the environment of the process, spawning, the handshake (``initialize`` then
``initialized``, with the check that the server is not older than the host speaks to), the in-memory login of an
API key, and stopping. A connection is ready for requests once ``open`` returns and is never reused after it
ended: the runtime opens a new one for the next turn. Nothing here knows about threads or turns.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from contextlib import AsyncExitStack

from myrm_agent_harness.toolkits.acp.auth._profiles import command_profile
from myrm_agent_harness.toolkits.acp.runtime._base import build_safe_env
from myrm_agent_harness.toolkits.acp.runtime.codex.protocol import (
    MINIMUM_SERVER_VERSION,
    initialize_params,
    login_params,
    server_version,
)
from myrm_agent_harness.toolkits.acp.runtime.codex.rpc import (
    JsonRpcPeer,
    NotificationObserver,
    RpcClosedError,
    RpcError,
    RpcTimeoutError,
    ServerRequestHandler,
)
from myrm_agent_harness.toolkits.acp.runtime.codex.wire import Json, as_str
from myrm_agent_harness.toolkits.acp.runtime.event_text import condense_failure_text
from myrm_agent_harness.toolkits.acp.runtime.process import ManagedProcess, spawn_managed_process
from myrm_agent_harness.toolkits.acp.types import RuntimeConfig

logger = logging.getLogger(__name__)

# A server that has not answered the handshake in this long is not going to: it starts in well under a second.
_HANDSHAKE_SECONDS = 30.0
# What a server that was asked to end gets to finish its own shutdown before it is stopped.
_CLOSE_GRACE_SECONDS = 2.0
# How long a process whose output ended is given to be reaped, so its exit code and last words can be reported.
_EXIT_WAIT_SECONDS = 2.0


def server_environment(name: str, config: RuntimeConfig, command: str) -> tuple[dict[str, str], str | None]:
    """The environment of the server, and the API key it is logged in with (an ``api_key`` agent only).

    The key is the first of the agent's provider key variables the config sets. It reaches the server through its
    login and is left out of the environment, which every command and MCP server the agent starts would inherit.
    """
    env = build_safe_env(config)
    if config.auth_mode != "api_key":
        return env, None
    profile = command_profile(command)
    names = profile.api_key_env if profile is not None else ()
    configured: Mapping[str, str] = config.env or {}
    key = next((configured[variable] for variable in names if configured.get(variable)), None)
    if key is None:
        logger.warning("codex_api_key_missing name=%s", name)
        return env, None
    for variable in names:
        env.pop(variable, None)
    return env, key


class AppServerStartError(Exception):
    """The app server could not be started or did not complete the handshake; the message says why.

    ``retryable`` is False when starting it again cannot help (an installed server that is too old).
    """

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class AppServerConnection:
    """A greeted app-server process and the peer that speaks to it."""

    def __init__(
        self,
        process: ManagedProcess,
        peer: JsonRpcPeer,
        stack: AsyncExitStack,
        version: tuple[int, int, int] | None,
    ) -> None:
        self.process = process
        self.peer = peer
        # The version the server said it is, None when it did not say.
        self.version = version
        self._stack = stack

    @property
    def alive(self) -> bool:
        return self.process.returncode is None and not self.peer.closed

    @classmethod
    async def open(
        cls,
        argv: list[str],
        *,
        env: Mapping[str, str],
        cwd: str,
        on_server_request: ServerRequestHandler,
        observe: NotificationObserver,
        api_key: str | None,
    ) -> AppServerConnection:
        """Start the server, shake hands and log in with ``api_key`` (when given).

        Raises:
            ProcessLaunchError: the executable does not exist.
            AppServerStartError: the process ended or stayed silent during the handshake, or refused the login.
        """
        stack = AsyncExitStack()
        try:
            process = await stack.enter_async_context(
                spawn_managed_process(argv, env=dict(env), cwd=cwd, interactive=True)
            )
            peer = JsonRpcPeer(process, on_server_request=on_server_request, observe=observe)
            stack.push_async_callback(peer.aclose)
            try:
                greeting = await peer.request("initialize", initialize_params(), timeout=_HANDSHAKE_SECONDS)
                version = _supported_version(greeting)
                await peer.notify("initialized")
                if api_key is not None:
                    await peer.request("account/login/start", login_params(api_key), timeout=_HANDSHAKE_SECONDS)
            except RpcClosedError:
                reason = await _exit_reason(process, "The Codex app server exited during startup")
                raise AppServerStartError(reason) from None
            except RpcTimeoutError:
                raise AppServerStartError("The Codex app server did not answer the handshake") from None
            except RpcError as exc:
                # The server's own words, never the request: a login request holds the API key.
                raise AppServerStartError(f"The Codex app server refused the handshake: {exc.message}") from None
        except BaseException:
            await stack.aclose()
            raise
        return cls(process, peer, stack, version)

    async def close(self, *, graceful: bool) -> None:
        """Stop the server: ``graceful`` lets it finish on its own after its input ends, otherwise it is stopped at once."""
        if graceful:
            self.process.close_stdin()
            with contextlib.suppress(TimeoutError):
                async with asyncio.timeout(_CLOSE_GRACE_SECONDS):
                    await self.process.wait()
        await self._stack.aclose()

    async def exit_reason(self) -> str:
        """What the process left behind (its exit code and the tail of what it wrote to stderr), for a failure message."""
        return await _exit_reason(self.process, "The Codex app server exited")


def _supported_version(greeting: Json) -> tuple[int, int, int] | None:
    """The version the server says it is, or None when it does not say.

    A server older than the host speaks to is refused; one that does not say its version is let through and noted.
    """
    version = server_version(greeting)
    if version is None:
        logger.warning("codex_server_version_unreadable user_agent=%r", as_str(greeting.get("userAgent"))[:120])
    elif version < MINIMUM_SERVER_VERSION:
        installed, needed = (".".join(map(str, parts)) for parts in (version, MINIMUM_SERVER_VERSION))
        msg = f"The installed Codex ({installed}) is too old: {needed} or newer is needed. Update Codex and try again."
        raise AppServerStartError(msg, retryable=False)
    return version


async def _exit_reason(process: ManagedProcess, prefix: str) -> str:
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(_EXIT_WAIT_SECONDS):
            await process.wait()
    detail = condense_failure_text(process.stderr_text)
    code = process.returncode
    head = f"{prefix} (code {code})" if code is not None else prefix
    return f"{head}: {detail}" if detail else head
