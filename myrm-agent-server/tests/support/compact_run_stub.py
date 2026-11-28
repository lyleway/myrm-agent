"""Keeps a test of the compact endpoints away from the database and the summary model.

[INPUT]
- app.services.chat.compact_service::compact_chat (POS: the service the run calls; replaced by the caller's stand-in)
- app.services.chat.compact.run::get_session (POS: the run's own database session; replaced by none)

[OUTPUT]
- compaction_answering(): while it is open, a manual compaction run calls the given stand-in with no session
- wait_for_run(): asks an endpoint for the run until it leaves ``running``

[POS]
A request to the compact endpoint only starts a run; the compaction happens afterwards on the event loop of the server,
and the page reads the outcome from a second endpoint. A test therefore holds the stand-in open across both requests.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from typing import cast
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.services.chat.compact._types import CompactResult

RUN_ROUTE = "/chat-1/compact/run"
_RUN_DEADLINE_S = 5.0

Compaction = Callable[..., Awaitable[CompactResult]]


@asynccontextmanager
async def _no_session() -> AsyncIterator[None]:
    yield None


@contextmanager
def compaction_answering(compaction: Compaction) -> Iterator[None]:
    with (
        patch("app.services.chat.compact_service.compact_chat", new=compaction),
        patch("app.services.chat.compact.run.get_session", new=_no_session),
    ):
        yield


def wait_for_run(client: TestClient, route: str = RUN_ROUTE) -> dict[str, object]:
    """The ``data`` of the run once it has ended. The run goes on in the loop thread of the client while this polls."""
    deadline = time.monotonic() + _RUN_DEADLINE_S
    while True:
        response = client.get(route)
        assert response.status_code == 200, response.text
        data = cast("dict[str, object]", response.json()["data"])
        if data["status"] != "running":
            return data
        assert time.monotonic() < deadline, f"the run is still running: {data}"
        time.sleep(0.01)
