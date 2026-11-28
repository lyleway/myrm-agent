"""A DevTools session of its own on one E2E page, next to the one the browser orchestrator holds.

The orchestrator evaluates scripts without a user gesture and offers no emulation, so the checks that need either talk to the
page over a session of their own. The session lives for one ``with`` block and leaves the page's other clients alone.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager

from chrome_mcp.client import McpPage
from websockets.sync.client import ClientConnection, connect

_CALL_TIMEOUT_SEC = 30.0


def _page_websocket_url(port: str, target_id: str) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
        targets: list[dict[str, object]] = json.load(response)
    for target in targets:
        if target.get("id") == target_id and target.get("webSocketDebuggerUrl"):
            return str(target["webSocketDebuggerUrl"])
    raise RuntimeError(f"DevTools target {target_id!r} is not listed on port {port}")


class DevToolsSession:
    """An open session on one page."""

    def __init__(self, connection: ClientConnection) -> None:
        self._connection = connection
        self._last_call_id = 0

    def call(self, method: str, params: dict[str, object]) -> dict[str, object]:
        """Send one command and return its result; an error answer from the browser raises."""
        self._last_call_id += 1
        call_id = self._last_call_id
        self._connection.send(json.dumps({"id": call_id, "method": method, "params": params}))
        while True:
            reply: dict[str, object] = json.loads(self._connection.recv(timeout=_CALL_TIMEOUT_SEC))
            if reply.get("id") == call_id:
                break
        if "error" in reply:
            raise RuntimeError(f"{method} failed: {reply['error']}")
        result = reply.get("result")
        return result if isinstance(result, dict) else {}


@contextmanager
def page_devtools_session(page: McpPage) -> Iterator[DevToolsSession]:
    """Open a session on ``page`` for the length of the block."""
    port = os.environ.get("MYRM_CHROME_E2E_PORT", "9333")
    with connect(_page_websocket_url(port, page.target_id), open_timeout=10, close_timeout=5) as connection:
        yield DevToolsSession(connection)
