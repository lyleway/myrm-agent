"""Loopback MCP endpoint through which Claude Code puts its permission prompts to the host.

A headless ``claude -p`` has nobody to ask before an edit or a command, so under ``--permission-mode default`` it
refuses. ``--permission-prompt-tool <mcp tool>`` hands that question to an MCP tool instead: the tool receives the
tool name and input and answers ``allow`` or ``deny``. This bridge is that tool, served on ``127.0.0.1`` for the
length of one turn and guarded by a per-turn bearer token, and what it answers is the host's.

[INPUT]
- toolkits.acp.runtime.turn_permissions::TurnPermissions (POS: The permission broker of one turn, as the backend of that turn sees it)
- toolkits.acp.types::PermissionRequest (POS: ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, serving as the foundation for the entire ACP module.)
- mcp.server.mcpserver::MCPServer, uvicorn::Server (POS: MCP server framework and the ASGI server it already depends on)

[OUTPUT]
- PermissionBridge: async context manager serving the approve tool; ``config_path`` is the ``--mcp-config`` file naming it
- PERMISSION_PROMPT_TOOL: the tool name Claude Code is told to ask

[POS]
CliRuntime's door for Claude permission prompts. Needs no host framework: the endpoint lives inside the harness
and a host only supplies the ``PermissionBroker``. Fails closed: an error while deciding answers ``deny``.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
import logging
import os
import secrets
import tempfile
from collections.abc import Iterator
from types import TracebackType

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Receive, Scope, Send

from myrm_agent_harness.toolkits.acp.runtime.turn_permissions import TurnPermissions
from myrm_agent_harness.toolkits.acp.types import PermissionRequest

logger = logging.getLogger(__name__)

_SERVER_NAME = "myrm_permissions"
_TOOL_NAME = "approve"
PERMISSION_PROMPT_TOOL = f"mcp__{_SERVER_NAME}__{_TOOL_NAME}"

_START_TIMEOUT_SECONDS = 10.0
# What a person is not waited for when the turn ends while a request is open: uvicorn then cancels the request.
_STOP_GRACE_SECONDS = 1
_DENIED_MESSAGE = "The user did not allow this action."
_TITLE_LIMIT = 120
# Claude refuses a prompt by itself after 60 seconds, and after 300 seconds without a reply, unless the server entry of
# its config says how long to wait. How long a person may take is the host's rule (its broker) and the end of the turn
# ends the wait, so the CLI is told to wait for up to a day.
_PROMPT_TIMEOUT_MS = 24 * 60 * 60 * 1000

# Claude Code's tool names, as the ACP tool kinds the host already understands. Anything else (an MCP tool, a
# subagent) is ``other``: asking about it is the point, and its input says what it is.
_KINDS = {
    "Read": "read",
    "NotebookRead": "read",
    "Glob": "search",
    "Grep": "search",
    "Edit": "edit",
    "MultiEdit": "edit",
    "Write": "edit",
    "NotebookEdit": "edit",
    "Bash": "execute",
    "BashOutput": "execute",
    "KillShell": "execute",
    "WebFetch": "fetch",
    "WebSearch": "fetch",
}
_PATH_KEYS = ("file_path", "notebook_path", "path")


class _EmbeddedServer(uvicorn.Server):
    """A uvicorn server that lives inside a host: the host keeps its own SIGINT/SIGTERM handling."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class _BearerGate:
    """ASGI gate: only a caller holding the turn's token reaches the MCP app (a local program, not a page)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            presented = dict(scope["headers"]).get(b"authorization", b"")
            if not hmac.compare_digest(presented, self._expected):
                await send({"type": "http.response.start", "status": 401, "headers": [(b"content-length", b"0")]})
                await send({"type": "http.response.body", "body": b""})
                return
        await self._app(scope, receive, send)


def _title(tool_name: str, tool_input: dict[str, object]) -> str:
    """One short line for the person asked: what the tool is about to do."""
    for key in ("description", "command", *_PATH_KEYS, "url"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return f"{tool_name}: {value.strip().splitlines()[0][:_TITLE_LIMIT]}"
    return tool_name


def _paths(tool_input: dict[str, object]) -> tuple[str, ...]:
    return tuple(value for key in _PATH_KEYS if isinstance(value := tool_input.get(key), str) and value)


class PermissionBridge:
    """The ``approve`` tool of one turn, served on a loopback port until the context exits."""

    def __init__(self, permissions: TurnPermissions, *, agent: str, session_id: str) -> None:
        self._permissions = permissions
        self._agent = agent
        self._session_id = session_id
        self._token = secrets.token_urlsafe(32)
        self._server: uvicorn.Server | None = None
        self._serving: asyncio.Task[None] | None = None
        self._config_path = ""

    @property
    def prompt_tool(self) -> str:
        """The tool name for ``--permission-prompt-tool``."""
        return PERMISSION_PROMPT_TOOL

    @property
    def config_path(self) -> str:
        """The ``--mcp-config`` file that connects Claude Code to this bridge (a file, so the token is not in argv)."""
        return self._config_path

    async def __aenter__(self) -> PermissionBridge:
        mcp = MCPServer(_SERVER_NAME)
        mcp.tool(
            name=_TOOL_NAME,
            description="Ask the host whether Claude Code may use a tool. Answers allow or deny.",
            structured_output=False,
        )(self._approve)
        app = mcp.streamable_http_app(
            stateless_http=True,
            json_response=True,
            streamable_http_path="/mcp",
            transport_security=TransportSecuritySettings(allowed_hosts=["127.0.0.1:*", "localhost:*"]),
        )
        config = uvicorn.Config(
            _BearerGate(app, self._token),
            host="127.0.0.1",
            port=0,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=_STOP_GRACE_SECONDS,
        )
        server = _EmbeddedServer(config)
        self._server = server
        self._serving = asyncio.create_task(server.serve(), name="myrm-permission-bridge")
        try:
            port = await asyncio.wait_for(self._listening_port(server), _START_TIMEOUT_SECONDS)
            self._config_path = self._write_config(port)
        except BaseException:
            await self._stop()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self._stop()

    async def _listening_port(self, server: uvicorn.Server) -> int:
        serving = self._serving
        while not server.started:
            if serving is not None and serving.done():
                # The server stopped before it listened (the port could not be bound).
                serving.result()
                msg = "The permission bridge stopped before it started listening"
                raise RuntimeError(msg)
            await asyncio.sleep(0.005)
        return int(server.servers[0].sockets[0].getsockname()[1])

    def _write_config(self, port: int) -> str:
        server: dict[str, object] = {
            "type": "http",
            "url": f"http://127.0.0.1:{port}/mcp",
            "headers": {"Authorization": f"Bearer {self._token}"},
            "timeout": _PROMPT_TIMEOUT_MS,
        }
        # ``mkstemp`` creates the file readable by this user only.
        descriptor, path = tempfile.mkstemp(prefix="myrm-permissions-", suffix=".json")
        with os.fdopen(descriptor, "w", encoding="utf-8") as config_file:
            json.dump({"mcpServers": {_SERVER_NAME: server}}, config_file)
        return path

    async def _stop(self) -> None:
        self._permissions.close()
        if self._config_path:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self._config_path)
        server, serving = self._server, self._serving
        if server is None or serving is None:
            return
        server.should_exit = True
        done, _ = await asyncio.wait({serving}, timeout=_STOP_GRACE_SECONDS + 2)
        if not done:
            serving.cancel()
            await asyncio.wait({serving})
        if not serving.cancelled() and serving.exception() is not None:
            logger.warning("permission_bridge_stop_failed agent=%s", self._agent, exc_info=serving.exception())

    async def _approve(self, tool_name: str, input: dict[str, object], tool_use_id: str | None = None) -> str:
        """Claude Code's permission prompt: its answer is the JSON text this returns."""
        request = PermissionRequest(
            agent=self._agent,
            session_id=self._session_id,
            tool_name=tool_name,
            kind=_KINDS.get(tool_name, "other"),
            title=_title(tool_name, input),
            input=input,
            paths=_paths(input),
            tool_call_id=tool_use_id,
        )
        verdict = await self._permissions.decide(request)
        if verdict.allowed:
            return json.dumps({"behavior": "allow", "updatedInput": input})
        return json.dumps({"behavior": "deny", "message": verdict.message or _DENIED_MESSAGE})
