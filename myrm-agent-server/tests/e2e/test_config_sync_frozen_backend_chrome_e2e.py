"""Chrome E2E (Lane-B, process fault): a backend that takes the request and never answers is cancelled at every attempt, and the edit lands once it answers again.

The other config-sync scenarios hold an answer back inside the page. Here the backend process itself is stopped
(`tests/e2e/config_sync_deadline/backend_freeze.py`): Chrome connects, sends the write and gets nothing back, as it
would from a backend stuck in a lock or a swapped-out host. Nothing is armed in the page's fetch shim, which only
records what the app did, so the app's own deadline, its abort of a real pending request and the outbox retry are what
is under test. The backend is thawed once the app has cancelled two attempts.

The writes the app cancelled may or may not have reached the backend once it runs again, so the test claims only what
holds either way: the edit is in the audit log exactly once, and the page shows it.
"""

from __future__ import annotations

import pytest

from tests.e2e.config_sync_deadline.assertions import (
    HEADERS_DEADLINE_MS,
    RETRY_PAUSE_MS,
    TIMER_LATE_MS,
    cancelled_at,
    elapsed,
    one_answered_write,
)
from tests.e2e.config_sync_deadline.backend_freeze import frozen_backend
from tests.e2e.config_sync_deadline.card_session import CardSession, run_marker
from tests.e2e.config_sync_deadline.scenario import deadline_card, run_phases

_CANCELS_BEFORE_THAW = 2
# A browser timer may fire a millisecond before its time.
_PAUSE_TOLERANCE_MS = 100.0


def _phase_frozen_backend(card: CardSession) -> dict[str, object]:
    """Every attempt to a silent backend is cancelled at the headers deadline; the retries back off 1 s, 2 s, and the
    first one the backend answers after it runs again is the edit's single write."""
    marker = run_marker("z")
    with frozen_backend(card.api_base) as backend:
        card.edit(marker)
        card.wait_for_cancelled_writes(
            marker,
            _CANCELS_BEFORE_THAW,
            timeout_sec=_CANCELS_BEFORE_THAW * (HEADERS_DEADLINE_MS + TIMER_LATE_MS) / 1000 + 90,
        )
        shown_while_frozen = card.shown()
        frozen_for = backend.thaw()
    writes = card.settled_writes(marker)
    *cancelled, answered = writes
    assert shown_while_frozen == marker, "the card dropped the typed key while the backend was silent"
    assert len(cancelled) >= _CANCELS_BEFORE_THAW and answered.status == 200, writes
    assert all(write.fault == "pass" for write in writes), f"the stall must be the backend's own, not the shim's: {writes}"
    for attempt in cancelled:
        assert (attempt.status, attempt.headers_at_ms) == (None, None), f"a frozen backend sent something back: {attempt}"
        waited = elapsed(attempt.started_at_ms, attempt.aborted_at_ms)
        assert HEADERS_DEADLINE_MS - 500 <= waited <= HEADERS_DEADLINE_MS + TIMER_LATE_MS, attempt
    pauses = [following.started_at_ms - cancelled_at(previous) for previous, following in zip(writes, writes[1:], strict=False)]
    for index, pause in enumerate(pauses):
        expected = RETRY_PAUSE_MS * 2**index
        assert expected - _PAUSE_TOLERANCE_MS <= pause <= expected + TIMER_LATE_MS, (
            f"retry {index + 1} waited {pause:.0f}ms, not {expected:.0f}ms"
        )
    assert card.applied_count(marker) == 1, "the edit must be in the audit log exactly once"
    assert card.stored() == marker and card.shown() == marker
    return {
        "frozen_for_s": round(frozen_for),
        "attempts": len(writes),
        "cancelled": len(cancelled),
        "pauses_ms": [round(pause) for pause in pauses],
    }


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="fault_injection",
)
@pytest.mark.integration
def test_a_frozen_backend_has_every_attempt_cancelled_and_the_edit_lands_once_it_answers_again() -> None:
    with deadline_card() as card:
        run_phases(
            card,
            {
                "frozen_backend": lambda: _phase_frozen_backend(card),
                "healthy_again": lambda: one_answered_write(card, run_marker("e")),
            },
        )
