"""A streamable-HTTP MCP server on a loopback port, for tests that hand a real agent an MCP server of the host's.

It is the shape of the endpoint a host serves its own tools on: stateless, the whole server behind a bearer token, one
tool (``lookup``, which answers ``found:<query>``). Every request is kept (the JSON-RPC method it carried and whether it
brought the token), so a test can tell that an agent connected, listed the tools and called one, and that it never got in
without the token.
"""

from __future__ import annotations

import asyncio
import contextlib
import hmac
import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

import uvicorn
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import ASGIApp, Message, Receive, Scope, Send

_START_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class McpRequest:
    """One request the endpoint was sent."""

    # The JSON-RPC method of a POST (``initialize``, ``tools/list``, ``tools/call``, ...); the HTTP verb otherwise.
    method: str
    authorized: bool
    # What the caller said in ``tools/call``: the tool and its arguments.
    tool: str = ""
    arguments: dict[str, object] = field(default_factory=dict)


class _EmbeddedServer(uvicorn.Server):
    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class _Recorder:
    """ASGI gate: records every request, and lets only the holder of the token through."""

    def __init__(self, app: ASGIApp, token: str, requests: list[McpRequest]) -> None:
        self._app = app
        self._expected = f"Bearer {token}".encode()
        self._requests = requests

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        authorized = hmac.compare_digest(dict(scope["headers"]).get(b"authorization", b""), self._expected)
        body = b""
        more = True
        while more:
            message = await receive()
            body += message.get("body", b"")
            more = message.get("more_body", False)
        self._requests.append(_recorded(scope["method"], body, authorized))
        if not authorized:
            await send({"type": "http.response.start", "status": 401, "headers": [(b"content-length", b"0")]})
            await send({"type": "http.response.body", "body": b""})
            return
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if replayed:
                return await receive()
            replayed = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self._app(scope, replay, send)


def _recorded(verb: str, body: bytes, authorized: bool) -> McpRequest:
    try:
        payload = json.loads(body) if body else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        return McpRequest(verb, authorized)
    params = payload.get("params")
    params = params if isinstance(params, dict) else {}
    arguments = params.get("arguments")
    return McpRequest(
        str(payload.get("method") or verb),
        authorized,
        tool=str(params.get("name", "")) if payload.get("method") == "tools/call" else "",
        arguments=arguments if isinstance(arguments, dict) else {},
    )


@dataclass
class RecordingMcpEndpoint:
    """The server: ``url`` and ``token`` are what an agent is given, ``requests`` what it did with them."""

    token: str = "loopback-mcp-token"
    server_name: str = "memory"
    requests: list[McpRequest] = field(default_factory=list, init=False)
    _server: uvicorn.Server | None = field(default=None, init=False, repr=False)
    _serving: asyncio.Task[None] | None = field(default=None, init=False, repr=False)
    _port: int = field(default=0, init=False, repr=False)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/mcp"

    @property
    def authorized_methods(self) -> list[str]:
        """The methods of the requests that brought the token, in order."""
        return [request.method for request in self.requests if request.authorized]

    @property
    def tool_calls(self) -> list[McpRequest]:
        return [request for request in self.requests if request.authorized and request.method == "tools/call"]

    async def listed_tools_within(self, seconds: float) -> bool:
        """Whether an agent with the token has asked for the tools by the time ``seconds`` have passed."""
        deadline = asyncio.get_running_loop().time() + seconds
        while "tools/list" not in self.authorized_methods:
            if asyncio.get_running_loop().time() > deadline:
                return False
            await asyncio.sleep(0.05)
        return True

    async def __aenter__(self) -> Self:
        mcp = MCPServer(self.server_name)

        @mcp.tool()
        def lookup(query: str) -> str:
            """Look the query up."""
            return f"found:{query}"

        app = mcp.streamable_http_app(
            stateless_http=True,
            streamable_http_path="/mcp",
            transport_security=TransportSecuritySettings(allowed_hosts=["127.0.0.1:*", "localhost:*"]),
        )
        config = uvicorn.Config(
            _Recorder(app, self.token, self.requests),
            host="127.0.0.1",
            port=0,
            log_level="warning",
            access_log=False,
            timeout_graceful_shutdown=1,
        )
        server = _EmbeddedServer(config)
        self._server = server
        self._serving = asyncio.create_task(server.serve(), name="recording-mcp-endpoint")
        deadline = asyncio.get_running_loop().time() + _START_TIMEOUT_SECONDS
        while not server.started:
            if self._serving.done() or asyncio.get_running_loop().time() > deadline:
                self._serving.result()
                msg = "The recording MCP endpoint did not start"
                raise RuntimeError(msg)
            await asyncio.sleep(0.005)
        self._port = int(server.servers[0].sockets[0].getsockname()[1])
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        if self._server is not None and self._serving is not None:
            self._server.should_exit = True
            await asyncio.wait({self._serving}, timeout=5)
