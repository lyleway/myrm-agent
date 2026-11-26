"""Pin the time zone one E2E page runs in, for the length of a block.

The override rides on a DevTools session of its own. The block holds that session open and lifts the override on the way
out, so a pinned zone never reaches another page of the shared browser and never outlives the check that asked for it.
"""

from __future__ import annotations

import json
import os
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager

from chrome_mcp.client import ChromeMcpClient, McpPage
from websockets.sync.client import ClientConnection, connect

_CALL_TIMEOUT_SEC = 15.0

_ZONE_NAME_JS = """(() => ({ timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone }))()"""


def _zone_name(client: ChromeMcpClient, page: McpPage) -> str:
    state = client.evaluate(page, _ZONE_NAME_JS, timeout_sec=15.0)
    assert isinstance(state, dict), state
    return str(state["timeZone"])


def _page_websocket_url(port: str, target_id: str) -> str:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as response:
        targets: list[dict[str, object]] = json.load(response)
    for target in targets:
        if target.get("id") == target_id and target.get("webSocketDebuggerUrl"):
            return str(target["webSocketDebuggerUrl"])
    raise RuntimeError(f"DevTools target {target_id!r} is not listed on port {port}")


def _set_time_zone(connection: ClientConnection, call_id: int, time_zone: str) -> None:
    """An empty name lifts the override."""
    connection.send(json.dumps({"id": call_id, "method": "Emulation.setTimezoneOverride", "params": {"timezoneId": time_zone}}))
    while True:
        reply = json.loads(connection.recv(timeout=_CALL_TIMEOUT_SEC))
        if reply.get("id") == call_id:
            break
    if "error" in reply:
        raise RuntimeError(f"Emulation.setTimezoneOverride({time_zone!r}) failed: {reply['error']}")


@contextmanager
def emulated_time_zone(client: ChromeMcpClient, page: McpPage, time_zone: str) -> Iterator[None]:
    """Run the block with ``page`` in the IANA zone ``time_zone``; the page's own zone is back when the block ends."""
    before = _zone_name(client, page)
    port = os.environ.get("MYRM_CHROME_E2E_PORT", "9333")
    with connect(_page_websocket_url(port, page.target_id), open_timeout=10, close_timeout=5) as connection:
        _set_time_zone(connection, 1, time_zone)
        try:
            assert _zone_name(client, page) == time_zone, "the page did not take the pinned time zone"
            yield
        finally:
            _set_time_zone(connection, 2, "")
    assert _zone_name(client, page) == before, "the page kept the pinned time zone"
