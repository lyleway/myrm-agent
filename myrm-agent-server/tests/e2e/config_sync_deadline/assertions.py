"""What every scenario of the config-sync deadline E2E measures against and asserts the same way.

The deadlines are those of ``fetchWithDeadlines`` (``adapters/BaseAdapter.ts``); a browser timer fires late under load,
never early. The page clock (``timeOrigin + now``) and the stamps the backend puts on its writes come from one machine
clock, so a small slack absorbs rounding when the two are compared.

[INPUT]
- tests.e2e.config_sync_deadline.card_session::CardSession (POS: Card layer of the config-sync deadline E2E: follows one
  edit from the keyboard to the database)
- tests.e2e.config_sync_deadline.fault_shim::ConfigRequest (POS: Fault injection layer of the config-sync deadline E2E;
  the only code that touches the page's fetch)

[OUTPUT]
- HEADERS_DEADLINE_MS / BODY_DEADLINE_MS: how long the app waits for an answer to start, and for its body to end
- TIMER_LATE_MS: how late a deadline timer may fire after its deadline
- CLOCK_SLACK_MS: rounding between the page clock and the backend's write stamps
- moment(page_ms) / elapsed(start_ms, end_ms) / cancelled_at(write): page moments a phase compares, each refusing a moment
  the request never reached
- one_answered_write(card, marker): one edit becomes one answered write that reaches the database once

[POS]
Assertion layer of the config-sync deadline E2E: the readings and checks the scenarios share.
"""

from __future__ import annotations

from tests.e2e.config_sync_deadline.card_session import CardSession
from tests.e2e.config_sync_deadline.fault_shim import ConfigRequest

HEADERS_DEADLINE_MS = 10_000
BODY_DEADLINE_MS = 30_000
TIMER_LATE_MS = 6_000
CLOCK_SLACK_MS = 250.0


def moment(page_ms: float | None) -> float:
    assert page_ms is not None, "the request never reached the point the assertion measures"
    return page_ms


def elapsed(start_ms: float | None, end_ms: float | None) -> float:
    return moment(end_ms) - moment(start_ms)


def cancelled_at(write: ConfigRequest) -> float:
    assert write.aborted_at_ms is not None, f"the app never cancelled the request: {write}"
    return write.aborted_at_ms


def one_answered_write(card: CardSession, marker: str) -> dict[str, object]:
    """Nothing armed: one edit is one answered write, so the shim itself does not disturb the sync."""
    card.edit(marker)
    card.wait_stored(marker)
    writes = card.settled_writes(marker)
    assert [(write.fault, write.status, write.aborted_at_ms) for write in writes] == [("pass", 200, None)], writes
    assert card.applied_count(marker) == 1, "one edit must reach the database once"
    return {"writes": len(writes)}
