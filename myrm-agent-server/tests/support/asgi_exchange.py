"""[INPUT]
- starlette.types (POS: ASGI callables and messages)

[OUTPUT]
- AsgiExchange: drive one HTTP request against an ASGI app with full control of both channels

[POS]
Test-only raw ASGI driver. TestClient and httpx buffer the whole response and always finish sending the
body first, which hides bugs that live in the receive channel (disconnect handling, ASGI spec versions) and in
streaming (a chunk must reach the client before the app is done). This driver models the server contract
instead: body chunks come out of ``receive`` first, then ``receive`` blocks until the client disconnects or
the response is complete, and every message the app sends is observable the moment it is sent.
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass, field

from starlette.types import ASGIApp, Message, Scope

_STEP_TIMEOUT_SECONDS = 5.0


@dataclass(slots=True)
class AsgiExchange:
    """One in-flight HTTP request. Start it, then read what the app sends and when.

    ``truncated`` makes the client drop the connection in the middle of the body.
    """

    app: ASGIApp
    method: str
    path: str
    headers: list[tuple[str, str]] = field(default_factory=list)
    body_chunks: tuple[bytes, ...] = (b"",)
    query_string: bytes = b""
    spec_version: str = "2.4"
    truncated: bool = False
    sent: asyncio.Queue[Message] = field(default_factory=asyncio.Queue)
    app_error: BaseException | None = None
    received: list[str] = field(default_factory=list)
    _pending_chunks: list[bytes] = field(default_factory=list)
    _released: asyncio.Event = field(default_factory=asyncio.Event)
    _task: asyncio.Task[None] | None = None

    def _scope(self) -> Scope:
        return {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": self.spec_version},
            "http_version": "1.1",
            "method": self.method,
            "scheme": "http",
            "path": self.path,
            "raw_path": self.path.encode(),
            "query_string": self.query_string,
            "root_path": "",
            "headers": [(name.lower().encode("latin-1"), value.encode("latin-1")) for name, value in self.headers],
            "client": ("127.0.0.1", 50000),
            "server": ("testserver", 80),
            "state": {},
        }

    async def _receive(self) -> Message:
        if self._pending_chunks:
            chunk = self._pending_chunks.pop(0)
            self.received.append("http.request")
            return {"type": "http.request", "body": chunk, "more_body": bool(self._pending_chunks) or self.truncated}
        if not self.truncated:
            await self._released.wait()
        self.received.append("http.disconnect")
        return {"type": "http.disconnect"}

    async def _send(self, message: Message) -> None:
        await self.sent.put(message)
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            self._released.set()

    async def _run(self) -> None:
        try:
            await self.app(self._scope(), self._receive, self._send)
        except BaseException as exc:
            self.app_error = exc
            raise
        finally:
            self._released.set()

    def start(self) -> None:
        self._pending_chunks = list(self.body_chunks)
        self._task = asyncio.create_task(self._run())

    async def next_message(self) -> Message:
        """The next message the app sends, or the app's own error if it failed first."""
        getter = asyncio.ensure_future(self.sent.get())
        assert self._task is not None
        done, _ = await asyncio.wait({getter, self._task}, timeout=_STEP_TIMEOUT_SECONDS, return_when=asyncio.FIRST_COMPLETED)
        if getter in done:
            return getter.result()
        getter.cancel()
        if self._task in done:
            self._task.result()
            raise AssertionError("The app finished without sending another message")
        raise AssertionError("The app sent nothing within the step timeout")

    def disconnect(self) -> None:
        """The client goes away: ``receive`` now yields ``http.disconnect``."""
        self._released.set()

    async def finish(self) -> None:
        """Wait for the app to return; re-raises whatever the app raised."""
        assert self._task is not None
        await asyncio.wait_for(self._task, timeout=_STEP_TIMEOUT_SECONDS)

    async def cancel(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    def drain(self) -> list[Message]:
        messages: list[Message] = []
        while not self.sent.empty():
            messages.append(self.sent.get_nowait())
        return messages


async def run_to_completion(exchange: AsgiExchange) -> tuple[int, dict[str, str], bytes]:
    """Run one exchange to the end and return its status, headers and whole body."""
    exchange.start()
    await exchange.finish()
    status = 0
    headers: dict[str, str] = {}
    body = bytearray()
    for message in exchange.drain():
        if message["type"] == "http.response.start":
            status = message["status"]
            headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in message["headers"]}
        elif message["type"] == "http.response.body":
            body.extend(message.get("body", b""))
    return status, headers, bytes(body)
