"""A permission broker for tests: the person a turn asks, answering the way the test says."""

from __future__ import annotations

import asyncio

from myrm_agent_harness.toolkits.acp.types import PermissionRequest, PermissionVerdict


class AnsweringBroker:
    """The host: answers every request with ``allowed`` (``None``: never answers) and keeps what it was asked.

    ``withdrawn`` tells that a request that was still waiting was given up (the turn ended or was stopped).
    """

    def __init__(self, allowed: bool | None) -> None:
        self._allowed = allowed
        self.asked: list[PermissionRequest] = []
        self.asked_event = asyncio.Event()
        self.withdrawn = False

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        self.asked.append(request)
        self.asked_event.set()
        if self._allowed is None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.withdrawn = True
                raise
        return PermissionVerdict(allowed=bool(self._allowed))
