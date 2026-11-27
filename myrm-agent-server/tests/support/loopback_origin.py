"""A loopback HTTP origin whose routes each test fills in, and the routes the outbound-fetch tests need.

[INPUT]
- The GET requests of the code under test (POS: an outbound fetch that a test points at ``url(...)``)

[OUTPUT]
- LoopbackOrigin: a server on a free loopback port; ``routes`` maps a request path to a Route, ``hits`` lists every path it was asked for
- Route: a function that answers one request
- page / redirect / endless / drip: the routes of a finite body, a redirect, a body that never ends and a body that arrives one byte at a time

[POS]
Server pytest-only. The tests of fetches that must refuse an address, follow a bounded number of redirects and cap the body
(the Open Graph preview, the RSS and Zotero connectors) run them against real sockets; ``hits`` shows that a refused address
was never connected to.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

Route = Callable[[BaseHTTPRequestHandler], None]


class LoopbackOrigin:
    def __init__(self) -> None:
        self.routes: dict[str, Route] = {}
        self.hits: list[str] = []
        origin = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                origin.hits.append(self.path)
                route = origin.routes.get(self.path)
                if route is None:
                    self.send_error(404)
                    return
                route(self)

            def log_message(self, format: str, *args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port: int = self._server.server_address[1]
        # A short poll interval keeps ``shutdown()`` from waiting half a second per test.
        threading.Thread(target=self._server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()

    def url(self, path: str, host: str = "127.0.0.1") -> str:
        return f"http://{host}:{self.port}{path}"

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()


def page(body: str, content_type: str = "text/html; charset=utf-8", status: int = 200) -> Route:
    def route(handler: BaseHTTPRequestHandler) -> None:
        data = body.encode()
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    return route


def redirect(location: str, status: int = 302) -> Route:
    def route(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(status)
        handler.send_header("Location", location)
        handler.send_header("Content-Length", "0")
        handler.end_headers()

    return route


def endless(head: str, content_type: str = "text/html") -> Route:
    """A page that starts with ``head`` and then streams filler for as long as the client listens."""

    def route(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.end_headers()
        try:
            handler.wfile.write(head.encode())
            while True:
                handler.wfile.write(b" " * 8192)
        except OSError:
            return

    return route


def drip(head: str, content_type: str = "text/html") -> Route:
    """A page that sends one byte every 50 ms, so no single read ever times out."""

    def route(handler: BaseHTTPRequestHandler) -> None:
        handler.send_response(200)
        handler.send_header("Content-Type", content_type)
        handler.end_headers()
        try:
            for byte in head.encode() * 600:
                handler.wfile.write(bytes([byte]))
                handler.wfile.flush()
                time.sleep(0.05)
        except OSError:
            return

    return route
