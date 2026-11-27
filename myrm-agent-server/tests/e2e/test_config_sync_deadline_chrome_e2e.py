"""Chrome E2E (Lane-B): the config sync layer cancels a stalled backend answer at its deadline and keeps the user's edits moving.

Real frontend, real backend, real database. An in-page fetch shim (`tests/e2e/config_sync_deadline/fault_shim.py`) delays or
withholds the answer of one `POST /config/sync` at a time, so the adapter's deadline timers, the outbox retry and the
version-conflict settlement run for real, and records when the app aborted each request: that tells a cancelled request
(its connection released) from one that was only abandoned. Edits are typed into the Remote Web Fetch Fallback card
(`tests/e2e/config_sync_deadline/card_session.py`) the way a user saves the Jina key. Each test is one scenario on a page of
its own (`tests/e2e/config_sync_deadline/scenario.py`); the scenario where the backend really stops answering is
`test_config_sync_frozen_backend_chrome_e2e.py`.

Every claim about timing rests on where the event happened: page times recorded by the shim, and the stamp the backend
puts on each write it applies. The test process only waits for the page to go quiet, so a slow link between the two (a
loaded host) delays the verdict without changing it.
"""

from __future__ import annotations

import pytest

from tests.e2e.config_sync_deadline.assertions import (
    BODY_DEADLINE_MS,
    CLOCK_SLACK_MS,
    HEADERS_DEADLINE_MS,
    TIMER_LATE_MS,
    cancelled_at,
    elapsed,
    moment,
    one_answered_write,
)
from tests.e2e.config_sync_deadline.card_session import CardSession, run_marker
from tests.e2e.config_sync_deadline.fault_shim import arm_fault
from tests.e2e.config_sync_deadline.scenario import DEFAULT_CONFIG, deadline_card, run_phases

_SLOW_HEADERS_MS = 4_000
_SLOW_BODY_MS = 12_000


def _phase_slow_headers(card: CardSession) -> dict[str, object]:
    """An answer later than a retry would be, but inside the headers deadline, is accepted: not cancelled, not sent twice."""
    marker = run_marker("s1")
    arm_fault(card.client, card.page, "slow_headers", marker, delay_ms=_SLOW_HEADERS_MS)
    card.edit(marker)
    card.wait_stored(marker)
    writes = card.settled_writes(marker)
    assert len(writes) == 1, writes
    write = writes[0]
    waited = elapsed(write.started_at_ms, write.headers_at_ms)
    assert (write.fault, write.status, write.aborted_at_ms) == ("slow_headers", 200, None), write
    assert _SLOW_HEADERS_MS - 500 <= waited < HEADERS_DEADLINE_MS, write
    assert card.applied_count(marker) == 1, "a slow answer must not make the app write again"
    return {"headers_wait_ms": round(waited), "writes": 1}


def _phase_slow_body(card: CardSession) -> dict[str, object]:
    """A body that ends after the headers deadline but inside the body deadline is read: the two deadlines are independent."""
    marker = run_marker("s2")
    arm_fault(card.client, card.page, "slow_body", marker, delay_ms=_SLOW_BODY_MS)
    card.edit(marker)
    card.wait_stored(marker)
    writes = card.settled_writes(marker)
    assert len(writes) == 1, writes
    write = writes[0]
    waited = elapsed(write.headers_at_ms, write.body_at_ms)
    assert (write.fault, write.status, write.aborted_at_ms) == ("slow_body", 200, None), write
    assert _SLOW_BODY_MS - 500 <= waited < BODY_DEADLINE_MS, write
    assert card.applied_count(marker) == 1, "a slow answer must not make the app write again"
    return {"body_wait_ms": round(waited), "writes": 1}


def _phase_stalled_headers(card: CardSession) -> dict[str, object]:
    """An answer that never starts, for a write that never reached the backend, is cancelled at the headers deadline and retried."""
    marker = run_marker("b")
    arm_fault(card.client, card.page, "hold_headers", marker, apply=False)
    card.edit(marker)
    first, *retries = card.settled_writes(marker, timeout_sec=HEADERS_DEADLINE_MS / 1000 + 90)
    cancelled_after = elapsed(first.started_at_ms, first.aborted_at_ms)
    assert (first.fault, first.status, first.headers_at_ms) == ("hold_headers", None, None), first
    assert HEADERS_DEADLINE_MS - 500 <= cancelled_after <= HEADERS_DEADLINE_MS + TIMER_LATE_MS, first
    assert retries and all(retry.fault == "pass" for retry in retries) and retries[-1].status == 200, retries
    assert retries[0].started_at_ms >= cancelled_at(first), "the retry started before the cancel"
    cancelled_wall = card.log().wall_ms(cancelled_at(first))
    applied_at = card.applied_at_ms(marker)
    assert applied_at >= cancelled_wall - CLOCK_SLACK_MS, "the backend took the write before the app gave up on the held attempt"
    assert card.stored() == marker
    return {"cancelled_after_ms": round(cancelled_after), "retries": len(retries)}


def _phase_stalled_body(card: CardSession) -> dict[str, object]:
    """The backend took the write but the answer's body never ends: cancelled at the body deadline, and the retry is
    accepted as a repeat of the write the backend holds, so the value lands once."""
    marker = run_marker("c")
    arm_fault(card.client, card.page, "stall_body", marker)
    card.edit(marker)
    first, *rest = card.settled_writes(marker, timeout_sec=BODY_DEADLINE_MS / 1000 + 90)
    cancelled_after = elapsed(first.headers_at_ms, first.aborted_at_ms)
    assert (first.fault, first.status, first.body_at_ms) == ("stall_body", 200, None), first
    assert BODY_DEADLINE_MS - 500 <= cancelled_after <= BODY_DEADLINE_MS + TIMER_LATE_MS, first
    assert rest and all(write.fault == "pass" for write in rest) and rest[-1].status == 200, rest
    assert rest[0].started_at_ms >= cancelled_at(first), "the retry started before the cancel"
    log, applied_at = card.log(), card.applied_at_ms(marker)
    attempt_began = log.wall_ms(first.started_at_ms) - CLOCK_SLACK_MS
    attempt_answered = log.wall_ms(moment(first.headers_at_ms)) + CLOCK_SLACK_MS
    assert attempt_began <= applied_at <= attempt_answered, (
        "the stalled answer must follow a write the backend took in that attempt"
    )
    assert card.stored() == marker and card.shown() == marker
    return {"cancelled_after_ms": round(cancelled_after), "retries": len(rest)}


def _phase_edit_during_stall(card: CardSession) -> dict[str, object]:
    """A newer edit made while an answer is withheld waits for the cancel, then replaces the edit whose answer was lost."""
    older, newer = run_marker("d1"), run_marker("d2")
    arm_fault(card.client, card.page, "hold_headers", older, apply=True)
    newer_edited_at = card.edit_while_held(older, newer)
    lost = card.settled_writes(older, timeout_sec=HEADERS_DEADLINE_MS / 1000 + 90)
    sent = card.settled_writes(newer, timeout_sec=HEADERS_DEADLINE_MS / 1000 + 90)
    assert len(lost) == 1 and lost[0].status is None, f"the lost edit must not be sent again: {lost}"
    older_cancelled_at = cancelled_at(lost[0])
    assert newer_edited_at < older_cancelled_at, "the newer edit came after the cancel: the stall window was missed"
    assert sent[0].started_at_ms >= older_cancelled_at, "the newer edit overtook the stalled answer"
    assert sent[-1].status == 200, sent
    assert card.server_reads_after(older_cancelled_at), "the newer edit never met the version conflict"
    cancelled_wall = card.log().wall_ms(older_cancelled_at)
    assert card.applied_at_ms(older) <= cancelled_wall + CLOCK_SLACK_MS, "the older edit must be the write whose answer was lost"
    assert card.applied_at_ms(newer) >= cancelled_wall - CLOCK_SLACK_MS, "the newer edit must land only after the cancel"
    assert card.stored() == newer and card.shown() == newer
    return {"older_cancelled_after_ms": round(elapsed(lost[0].started_at_ms, older_cancelled_at)), "newer_writes": len(sent)}


def _phase_settlement_read_stalls(card: CardSession) -> dict[str, object]:
    """The read that settles a version conflict stalls: it is cancelled at its deadline, the newer edit stays queued, and
    lands once, only after a later read has settled the conflict."""
    older, newer = run_marker("g1"), run_marker("g2")
    arm_fault(card.client, card.page, "hold_headers", older, apply=True)
    card.edit_while_held(older, newer, hold_settlement_read=True)
    patience = 2 * HEADERS_DEADLINE_MS / 1000 + 90
    lost = card.settled_writes(older, timeout_sec=patience)
    sent = card.settled_writes(newer, timeout_sec=patience)
    assert len(lost) == 1 and lost[0].status is None, f"the lost edit must not be sent again: {lost}"
    older_cancelled_at = cancelled_at(lost[0])
    reads = card.server_reads_after(older_cancelled_at)
    assert reads, "the newer edit never met the version conflict"
    stalled, *later = reads
    stalled_after = elapsed(stalled.started_at_ms, stalled.aborted_at_ms)
    assert (stalled.fault, stalled.status) == ("hold_headers", None), reads
    assert HEADERS_DEADLINE_MS - 500 <= stalled_after <= HEADERS_DEADLINE_MS + TIMER_LATE_MS, stalled
    assert later and all(read.fault == "pass" for read in later) and later[-1].status == 200, reads
    settled_read = later[-1]
    assert settled_read.started_at_ms >= cancelled_at(stalled), "the next read started before the stalled one was cancelled"
    answered_at = moment(settled_read.headers_at_ms)
    assert sent[-1].status == 200 and sent[-1].started_at_ms >= answered_at, (
        "the edit was re-sent before the conflict was settled"
    )
    log = card.log()
    assert card.applied_at_ms(older) <= log.wall_ms(older_cancelled_at) + CLOCK_SLACK_MS, (
        "the older edit must be the write whose answer was lost"
    )
    assert card.applied_at_ms(newer) >= log.wall_ms(answered_at) - CLOCK_SLACK_MS, (
        "the newer edit landed before the conflict was settled"
    )
    assert card.stored() == newer and card.shown() == newer
    return {"read_cancelled_after_ms": round(stalled_after), "reads": len(reads), "newer_writes": len(sent)}


def _phase_fresh_page(card: CardSession, stored_key: str) -> dict[str, object]:
    """A fresh page has no memory of an earlier session: its card shows what the backend stores, and its first edit lands once.

    The switch and the key the page shows can only come from the stored record, which the page's read path must have
    delivered before the card mounted.
    """
    switch = card.enabled_switch()
    assert switch == "true", f"the fresh card shows the enabled switch as {switch!r} while the backend holds it on"
    shown = card.shown()
    assert shown == stored_key, f"the fresh card shows the Jina key {shown!r} while the backend holds {stored_key!r}"
    return one_answered_write(card, run_marker("f"))


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.integration
def test_answers_inside_the_deadlines_are_accepted_and_written_once() -> None:
    with deadline_card() as card:
        run_phases(
            card,
            {
                "healthy": lambda: one_answered_write(card, run_marker("a")),
                "slow_headers": lambda: _phase_slow_headers(card),
                "slow_body": lambda: _phase_slow_body(card),
            },
        )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.integration
def test_an_answer_that_never_starts_is_cancelled_at_its_deadline_and_the_edit_lands_once() -> None:
    with deadline_card() as card:
        run_phases(
            card,
            {
                "stalled_headers": lambda: _phase_stalled_headers(card),
                "healthy_again": lambda: one_answered_write(card, run_marker("e")),
            },
        )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.integration
def test_an_answer_whose_body_never_ends_is_cancelled_at_its_deadline_and_the_edit_lands_once() -> None:
    with deadline_card() as card:
        run_phases(
            card,
            {
                "stalled_body": lambda: _phase_stalled_body(card),
                "healthy_again": lambda: one_answered_write(card, run_marker("e")),
            },
        )


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.integration
def test_an_edit_made_during_a_stall_replaces_the_edit_whose_answer_was_lost() -> None:
    with deadline_card() as card:
        run_phases(card, {"edit_during_stall": lambda: _phase_edit_during_stall(card)})


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.integration
def test_a_stalled_conflict_settlement_read_is_cancelled_and_the_edit_lands_once() -> None:
    with deadline_card() as card:
        run_phases(card, {"settlement_read_stalls": lambda: _phase_settlement_read_stalls(card)})


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.integration
def test_a_fresh_page_shows_the_stored_config_and_its_first_edit_lands_once() -> None:
    stored_key = run_marker("r")
    with deadline_card({**DEFAULT_CONFIG, "enabled": True, "jinaApiKey": stored_key}) as card:
        run_phases(card, {"fresh_page": lambda: _phase_fresh_page(card, stored_key)})
