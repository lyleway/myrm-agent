"""Chrome E2E (Lane-B, process fault): a backend that takes the write and never answers has it cancelled at its deadline, and the edit lands once it answers again.

The other config-sync scenarios hold an answer back inside the page. Here the backend process itself is stopped
(`tests/e2e/config_sync_deadline/backend_freeze.py`): Chrome connects, sends the write and gets nothing back, as it
would from a backend stuck in a lock or a swapped-out host. Nothing is armed in the page's fetch shim, which only
records what the app did, so the app's own deadline and its abort of a real pending request are what is under test.

The page of a local deployment asks the backend whether it is ready before each retry, and a silent backend does not
answer that either, so no second write is sent while it stays silent. The test therefore claims only what holds
either way: the app cancels the write it sent at its deadline, shows the edit meanwhile, recovers on its own once the
backend runs again, and the edit is in the audit log exactly once, whether or not the cancelled write reached the
backend after it ran again.
"""

from __future__ import annotations

import time

import pytest

from tests.e2e.config_sync_deadline.assertions import HEADERS_DEADLINE_MS, TIMER_LATE_MS, elapsed, one_answered_write
from tests.e2e.config_sync_deadline.backend_freeze import frozen_backend
from tests.e2e.config_sync_deadline.card_session import CardSession, run_marker
from tests.e2e.config_sync_deadline.scenario import deadline_card, run_phases

# The backend stays frozen until the app has cancelled its write; the watchdog resumes it only if this test dies first.
_CANCEL_WAIT_SEC = (HEADERS_DEADLINE_MS + TIMER_LATE_MS) / 1000 + 30
_RECOVERY_WAIT_SEC = 60.0


def _phase_frozen_backend(card: CardSession) -> dict[str, object]:
    """The write to a silent backend is cancelled at the headers deadline; once the backend answers, the edit is written once."""
    marker = run_marker("z")
    with frozen_backend(card.api_base, ttl_sec=2 * _CANCEL_WAIT_SEC) as backend:
        card.edit(marker)
        card.wait_for_cancelled_writes(marker, 1, timeout_sec=_CANCEL_WAIT_SEC)
        shown_while_frozen = card.shown()
        frozen_for = backend.thaw()
    thawed = time.monotonic()
    card.wait_stored(marker, timeout_sec=_RECOVERY_WAIT_SEC)
    recovered_in = time.monotonic() - thawed
    writes = card.settled_writes(marker)
    *cancelled, answered = writes
    assert shown_while_frozen == marker, "the card dropped the typed key while the backend was silent"
    assert cancelled and answered.status == 200, writes
    assert all(write.fault == "pass" for write in writes), f"the stall must be the backend's own, not the shim's: {writes}"
    for attempt in cancelled:
        assert (attempt.status, attempt.headers_at_ms) == (None, None), f"a frozen backend sent something back: {attempt}"
        waited = elapsed(attempt.started_at_ms, attempt.aborted_at_ms)
        assert HEADERS_DEADLINE_MS - 500 <= waited <= HEADERS_DEADLINE_MS + TIMER_LATE_MS, attempt
    assert card.applied_count(marker) == 1, "the edit must be in the audit log exactly once"
    assert card.stored() == marker and card.shown() == marker
    return {
        "frozen_for_s": round(frozen_for),
        "recovered_in_s": round(recovered_in, 1),
        "attempts": len(writes),
        "cancelled": len(cancelled),
    }


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="fault_injection",
)
@pytest.mark.integration
def test_a_frozen_backend_has_the_write_cancelled_at_its_deadline_and_the_edit_lands_once_it_answers_again() -> None:
    with deadline_card() as card:
        run_phases(
            card,
            {
                "frozen_backend": lambda: _phase_frozen_backend(card),
                "healthy_again": lambda: one_answered_write(card, run_marker("e")),
            },
        )
