"""A JSON-RPC peer over the stdio of Codex's app server: requests out, notifications and server requests in.

The app server speaks JSON-RPC without the ``jsonrpc`` member, one message per line. One reader task takes every line
off stdout: a response resolves the request that is waiting for it, a notification is observed at once (in wire order,
before anything that came after it is handled) and queued for the turn loop, and a request of the server (an approval)
is answered by its own task, so a person taking minutes to decide never stops the reader.

[INPUT]
- toolkits.acp.runtime.process::ManagedProcess (POS: Process ownership of every runtime that starts an agent)

[OUTPUT]
- JsonRpcPeer: the connection: ``request``, ``notify``, ``notifications``, ``discard_notifications``, ``aclose``
- Notification: one notification of the server
- RpcError, RpcClosedError, RpcTimeoutError: the ways a request fails
- ServerRequestHandler, NotificationObserver: what the owner supplies to answer the server and to watch it

[POS]
Transport of CodexRuntime. Knows nothing about threads, turns or approvals: it only moves messages. Params of a request
are never logged (a login request carries an API key); every message is a single ASCII line, so no text in it can
break the framing.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from dataclasses import dataclass

from myrm_agent_harness.toolkits.acp.runtime.process import ManagedProcess

logger = logging.getLogger(__name__)

# The JSON-RPC error codes this peer answers a request of the server with.
METHOD_NOT_FOUND = -32601
_INTERNAL_ERROR = -32603


@dataclass(frozen=True, slots=True)
class Notification:
    """One notification of the server."""

    method: str
    params: Mapping[str, object]


class RpcError(Exception):
    """The server answered a request with an error; a handler raises it to answer the server's request with one."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"{message} (code {code})")
        self.code = code
        self.message = message


class RpcClosedError(Exception):
    """The connection ended: the process left or its pipes closed."""


class RpcTimeoutError(Exception):
    """A request was not answered in time."""


type ServerRequestHandler = Callable[[str, Mapping[str, object]], Awaitable[Mapping[str, object]]]
type NotificationObserver = Callable[[Notification], None]


class JsonRpcPeer:
    """The connection to one app-server process. Requests may be made from several tasks at once."""

    def __init__(
        self,
        process: ManagedProcess,
        *,
        on_server_request: ServerRequestHandler,
        observe: NotificationObserver,
    ) -> None:
        self._process = process
        self._on_server_request = on_server_request
        self._observe = observe
        self._pending: dict[int, asyncio.Future[Mapping[str, object]]] = {}
        self._notifications: asyncio.Queue[Notification | None] = asyncio.Queue()
        self._handlers: set[asyncio.Task[None]] = set()
        self._next_id = 1
        self._closed = False
        self._reader = asyncio.create_task(self._read())

    @property
    def closed(self) -> bool:
        """Whether the connection has ended (the process left, or ``aclose`` ran)."""
        return self._closed

    async def request(
        self, method: str, params: Mapping[str, object] | None = None, *, timeout: float
    ) -> Mapping[str, object]:
        """Send a request and wait for its result.

        Raises:
            RpcError: the server answered with an error.
            RpcClosedError: the connection ended before an answer came.
            RpcTimeoutError: no answer came within ``timeout`` seconds.
        """
        if self._closed:
            raise RpcClosedError("The app server connection is closed")
        request_id = self._next_id
        self._next_id += 1
        answer: asyncio.Future[Mapping[str, object]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = answer
        try:
            async with asyncio.timeout(timeout):
                await self._write({"id": request_id, "method": method, "params": dict(params or {})})
                return await answer
        except TimeoutError:
            raise RpcTimeoutError(f"{method} was not answered within {timeout:g}s") from None
        finally:
            self._pending.pop(request_id, None)

    async def notify(self, method: str, params: Mapping[str, object] | None = None) -> None:
        """Send a notification, which has no answer."""
        message: dict[str, object] = {"method": method}
        if params is not None:
            message["params"] = dict(params)
        await self._write(message)

    async def notifications(self, idle_tick: float) -> AsyncGenerator[Notification | None]:
        """The notifications as they arrive, a ``None`` each ``idle_tick`` seconds without one; ends with the connection."""
        while True:
            try:
                notification = await asyncio.wait_for(self._notifications.get(), idle_tick)
            except TimeoutError:
                yield None
                continue
            if notification is None:
                self._notifications.put_nowait(None)
                return
            yield notification

    def discard_notifications(self) -> None:
        """Drop the notifications nobody read (the late ones of a turn that is over); the end of the connection stays."""
        ended = False
        while True:
            try:
                ended = self._notifications.get_nowait() is None or ended
            except asyncio.QueueEmpty:
                break
        if ended:
            self._notifications.put_nowait(None)

    async def aclose(self) -> None:
        """Stop reading and abandon every request that is still waiting; the process itself is the owner's to stop."""
        self._reader.cancel()
        for handler in tuple(self._handlers):
            handler.cancel()
        await asyncio.wait({self._reader, *self._handlers})
        self._end()

    async def _write(self, message: Mapping[str, object]) -> None:
        try:
            await self._process.send(json.dumps(message, separators=(",", ":")))
        except OSError as exc:
            raise RpcClosedError("The app server takes no more input") from exc

    async def _read(self) -> None:
        try:
            async for line in self._process.lines():
                if line is not None:
                    self._dispatch(line)
        finally:
            self._end()

    def _end(self) -> None:
        """Mark the connection ended: every waiting request fails and the notification stream gets its end marker."""
        if self._closed:
            return
        self._closed = True
        for answer in self._pending.values():
            if not answer.done():
                answer.set_exception(RpcClosedError("The app server closed the connection"))
        self._notifications.put_nowait(None)
        for handler in tuple(self._handlers):
            handler.cancel()

    def _dispatch(self, line: str) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            logger.warning("codex_rpc_non_json_line chars=%d", len(line))
            return
        if not isinstance(message, dict):
            logger.warning("codex_rpc_unexpected_message chars=%d", len(line))
            return
        method = message.get("method")
        message_id = message.get("id")
        if isinstance(method, str):
            raw_params = message.get("params")
            params: Mapping[str, object] = raw_params if isinstance(raw_params, dict) else {}
            if message_id is None:
                self._receive(Notification(method, params))
            else:
                handler = asyncio.create_task(self._serve(message_id, method, params))
                self._handlers.add(handler)
                handler.add_done_callback(self._handlers.discard)
        elif isinstance(message_id, int) and ("result" in message or "error" in message):
            self._resolve(message_id, message)

    def _receive(self, notification: Notification) -> None:
        try:
            self._observe(notification)
        except Exception:
            logger.warning("codex_rpc_observer_failed method=%s", notification.method, exc_info=True)
        self._notifications.put_nowait(notification)

    def _resolve(self, message_id: int, message: Mapping[str, object]) -> None:
        answer = self._pending.get(message_id)
        if answer is None or answer.done():
            return
        error = message.get("error")
        if isinstance(error, dict):
            code = error.get("code")
            answer.set_exception(
                RpcError(code if isinstance(code, int) else _INTERNAL_ERROR, str(error.get("message", "")))
            )
            return
        result = message.get("result")
        answer.set_result(result if isinstance(result, dict) else {})

    async def _serve(self, message_id: object, method: str, params: Mapping[str, object]) -> None:
        """Answer one request of the server with what the owner's handler returns."""
        reply: dict[str, object]
        try:
            reply = {"id": message_id, "result": await self._on_server_request(method, params)}
        except RpcError as exc:
            reply = {"id": message_id, "error": {"code": exc.code, "message": exc.message}}
        except Exception:
            logger.warning("codex_rpc_server_request_failed method=%s", method, exc_info=True)
            reply = {"id": message_id, "error": {"code": _INTERNAL_ERROR, "message": "Internal error"}}
        with contextlib.suppress(RpcClosedError):
            await self._write(reply)
