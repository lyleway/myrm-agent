"""The permission broker of one turn, as the backend of that turn sees it.

[INPUT]
- toolkits.acp.types::PermissionBroker, PermissionRequest, PermissionVerdict (POS: ACP runtime type definitions layer. Provides all ACP-related core abstractions and data structures, serving as the foundation for the entire ACP module.)

[OUTPUT]
- TurnPermissions: the host's broker with the three guarantees every backend needs of it

[POS]
Turn-scoped guard around the host's ``PermissionBroker``. A backend that asks it never has to handle the broker
failing, the person taking long, or the turn ending first: a failing broker refuses the request; the turn's time
limit (``timeout_seconds``, what the agent spends working) stands still while any request waits for a person; and
the requests still waiting when the turn ends are cancelled, so the person is no longer asked about a turn that is over.
"""

from __future__ import annotations

import asyncio
import logging

from myrm_agent_harness.toolkits.acp.types import PermissionBroker, PermissionRequest, PermissionVerdict

logger = logging.getLogger(__name__)

_TURN_ENDED = "The turn ended before the request was answered."


class TurnPermissions:
    """Wraps a turn's broker; ``decide`` never raises except when its own caller is cancelled."""

    def __init__(self, broker: PermissionBroker, clock: asyncio.Timeout) -> None:
        self._broker = broker
        self._clock = clock
        self._pending: set[asyncio.Future[PermissionVerdict]] = set()
        self._budget_left: float | None = None
        self._closed = False

    async def decide(self, request: PermissionRequest) -> PermissionVerdict:
        if self._closed:
            return PermissionVerdict(allowed=False, message=_TURN_ENDED)
        decision = asyncio.ensure_future(self._broker.decide(request))
        self._lift_deadline(decision)
        try:
            return await decision
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise
            return PermissionVerdict(allowed=False, message=_TURN_ENDED)
        except Exception:
            logger.warning("permission_broker_failed agent=%s tool=%s", request.agent, request.tool_name, exc_info=True)
            return PermissionVerdict(allowed=False)
        finally:
            self._restore_deadline(decision)

    def close(self) -> None:
        """End the turn's requests: every decision still waiting is cancelled, and a later request is refused."""
        self._closed = True
        for decision in tuple(self._pending):
            decision.cancel()

    def _lift_deadline(self, decision: asyncio.Future[PermissionVerdict]) -> None:
        """Take the turn's deadline off while a request waits; requests wait side by side (parallel tool calls)."""
        self._pending.add(decision)
        if len(self._pending) > 1 or self._clock.expired():
            return
        deadline = self._clock.when()
        self._budget_left = None if deadline is None else max(deadline - asyncio.get_running_loop().time(), 0.0)
        self._clock.reschedule(None)

    def _restore_deadline(self, decision: asyncio.Future[PermissionVerdict]) -> None:
        """Set the deadline again, with the budget that was left, once the last waiting request is answered."""
        self._pending.discard(decision)
        # A request that ends because the turn did runs this after the turn's clock has been left, and a clock that has
        # been left cannot be set again.
        if self._closed or self._pending or self._budget_left is None or self._clock.expired():
            return
        self._clock.reschedule(asyncio.get_running_loop().time() + self._budget_left)
        self._budget_left = None
