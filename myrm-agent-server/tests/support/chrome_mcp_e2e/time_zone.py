"""Pin the time zone one E2E page runs in, for the length of a block.

The override rides on a DevTools session of its own. The block holds that session open and lifts the override on the way
out, so a pinned zone never reaches another page of the shared browser and never outlives the check that asked for it.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from chrome_mcp.client import ChromeMcpClient, McpPage

from .devtools_session import DevToolsSession, page_devtools_session

_ZONE_NAME_JS = """(() => ({ timeZone: Intl.DateTimeFormat().resolvedOptions().timeZone }))()"""


def _zone_name(client: ChromeMcpClient, page: McpPage) -> str:
    state = client.evaluate(page, _ZONE_NAME_JS, timeout_sec=15.0)
    assert isinstance(state, dict), state
    return str(state["timeZone"])


def _set_time_zone(session: DevToolsSession, time_zone: str) -> None:
    """An empty name lifts the override."""
    session.call("Emulation.setTimezoneOverride", {"timezoneId": time_zone})


@contextmanager
def emulated_time_zone(client: ChromeMcpClient, page: McpPage, time_zone: str) -> Iterator[None]:
    """Run the block with ``page`` in the IANA zone ``time_zone``; the page's own zone is back when the block ends."""
    before = _zone_name(client, page)
    with page_devtools_session(page) as session:
        _set_time_zone(session, time_zone)
        try:
            assert _zone_name(client, page) == time_zone, "the page did not take the pinned time zone"
            yield
        finally:
            _set_time_zone(session, "")
    assert _zone_name(client, page) == before, "the page kept the pinned time zone"
