"""A loopback HTTP server a real agent binary can be pointed at instead of a model provider.

The fake model APIs (``_fake_anthropic.py``, ``_fake_model_apis.py``) differ in the wire format they speak and share
the rest: they listen on a free port of ``127.0.0.1`` for as long as the test needs them, keep every JSON body they were
sent so a test can read what the model was told, and answer any route they do not script with a bare JSON acknowledgement (the
side requests an agent makes, such as a token count or a title).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from types import TracebackType
from typing import Self

from aiohttp import web


@dataclass
class LoopbackApi:
    """The server's lifecycle. A subclass names its routes in ``_register`` and answers them."""

    requests: list[dict[str, object]] = field(default_factory=list, init=False)
    _runner: web.AppRunner | None = field(default=None, init=False, repr=False)
    _port: int = field(default=0, init=False, repr=False)

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    def _register(self, app: web.Application) -> None:
        """Add the scripted routes to ``app``; the catch-all that follows answers everything else."""
        raise NotImplementedError

    async def __aenter__(self) -> Self:
        app = web.Application()
        self._register(app)
        app.router.add_route("*", "/{tail:.*}", self._anything_else)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        await web.TCPSite(self._runner, "127.0.0.1", 0).start()
        (host_and_port,) = self._runner.addresses
        self._port = int(host_and_port[1])
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        if self._runner is not None:
            await self._runner.cleanup()

    async def _anything_else(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True})
