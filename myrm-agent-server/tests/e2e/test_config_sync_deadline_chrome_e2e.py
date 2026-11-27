"""Chrome E2E (Lane-B): the config sync layer cancels a stalled backend answer at its deadline and keeps the user's edits moving.

Real frontend, real backend, real database. An in-page fetch shim (`tests/e2e/config_sync_deadline/fault_shim.py`) delays or
withholds the answer of one `POST /config/sync` at a time, so the adapter's deadline timers, the outbox retry and the
version-conflict settlement run for real, and records when the app aborted each request: that tells a cancelled request
(its connection released) from one that was only abandoned. Edits are typed into the Remote Web Fetch Fallback card
(`tests/e2e/config_sync_deadline/card_session.py`) the way a user saves the Jina key.

Every claim about timing rests on where the event happened: page times recorded by the shim, and the stamp the backend
puts on each write it applies. The test process only waits for the page to go quiet, so a slow link between the two (a
loaded host) delays the verdict without changing it.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable

import pytest
from cdp_chat.support import fetch_config_value, get_e2e_api_url, put_config_value, wait_e2e_provider_ready

from tests.e2e.config_sync_deadline.card_session import CONFIG_KEY, SETTINGS_ROUTE, CardSession, run_marker
from tests.e2e.config_sync_deadline.fault_shim import ConfigRequest, arm_fault, install_fault_shim
from tests.support.chrome_mcp_e2e import open_settings_subroute, warm_ui_route

_DEFAULTS: dict[str, object] = {
    "enabled": False,
    "jinaApiKey": None,
    "firecrawl": {"inheritFromSearch": True, "api_key": None, "apiBase": None, "networkIsolationConfirmed": False},
    "sessionCap": 5,
}

# The deadlines of `fetchWithDeadlines` (adapters/BaseAdapter.ts); a browser timer fires late under load, never early.
_HEADERS_DEADLINE_MS = 10_000
_BODY_DEADLINE_MS = 30_000
_TIMER_LATE_MS = 6_000
_SLOW_HEADERS_MS = 4_000
_SLOW_BODY_MS = 12_000
# The page clock (`timeOrigin + now`) and the backend's write stamps come from one machine clock; this absorbs rounding.
_CLOCK_SLACK_MS = 250.0


def _moment(page_ms: float | None) -> float:
    assert page_ms is not None, "the request never reached the point the assertion measures"
    return page_ms


def _elapsed(start_ms: float | None, end_ms: float | None) -> float:
    return _moment(end_ms) - _moment(start_ms)


def _cancelled_at(write: ConfigRequest) -> float:
    assert write.aborted_at_ms is not None, f"the app never cancelled the request: {write}"
    return write.aborted_at_ms


def _phase_healthy(card: CardSession, marker: str) -> dict[str, object]:
    """Nothing armed: one edit is one answered write, so the shim itself does not disturb the sync."""
    card.edit(marker)
    card.wait_stored(marker)
    writes = card.settled_writes(marker)
    assert [(write.fault, write.status, write.aborted_at_ms) for write in writes] == [("pass", 200, None)], writes
    assert card.applied_count(marker) == 1, "one edit must reach the database once"
    return {"writes": len(writes)}


def _phase_slow_headers(card: CardSession) -> dict[str, object]:
    """An answer later than a retry would be, but inside the headers deadline, is accepted: not cancelled, not sent twice."""
    marker = run_marker("s1")
    arm_fault(card.client, card.page, "slow_headers", marker, delay_ms=_SLOW_HEADERS_MS)
    card.edit(marker)
    card.wait_stored(marker)
    writes = card.settled_writes(marker)
    assert len(writes) == 1, writes
    write = writes[0]
    waited = _elapsed(write.started_at_ms, write.headers_at_ms)
    assert (write.fault, write.status, write.aborted_at_ms) == ("slow_headers", 200, None), write
    assert _SLOW_HEADERS_MS - 500 <= waited < _HEADERS_DEADLINE_MS, write
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
    waited = _elapsed(write.headers_at_ms, write.body_at_ms)
    assert (write.fault, write.status, write.aborted_at_ms) == ("slow_body", 200, None), write
    assert _SLOW_BODY_MS - 500 <= waited < _BODY_DEADLINE_MS, write
    assert card.applied_count(marker) == 1, "a slow answer must not make the app write again"
    return {"body_wait_ms": round(waited), "writes": 1}


def _phase_stalled_headers(card: CardSession) -> dict[str, object]:
    """An answer that never starts, for a write that never reached the backend, is cancelled at the headers deadline and retried."""
    marker = run_marker("b")
    arm_fault(card.client, card.page, "hold_headers", marker, apply=False)
    card.edit(marker)
    first, *retries = card.settled_writes(marker, timeout_sec=_HEADERS_DEADLINE_MS / 1000 + 90)
    cancelled_after = _elapsed(first.started_at_ms, first.aborted_at_ms)
    assert (first.fault, first.status, first.headers_at_ms) == ("hold_headers", None, None), first
    assert _HEADERS_DEADLINE_MS - 500 <= cancelled_after <= _HEADERS_DEADLINE_MS + _TIMER_LATE_MS, first
    assert retries and all(retry.fault == "pass" for retry in retries) and retries[-1].status == 200, retries
    assert retries[0].started_at_ms >= _cancelled_at(first), "the retry started before the cancel"
    cancelled_wall = card.log().wall_ms(_cancelled_at(first))
    applied_at = card.applied_at_ms(marker)
    assert applied_at >= cancelled_wall - _CLOCK_SLACK_MS, "the backend took the write before the app gave up on the held attempt"
    assert card.stored() == marker
    return {"cancelled_after_ms": round(cancelled_after), "retries": len(retries)}


def _phase_stalled_body(card: CardSession) -> dict[str, object]:
    """The backend took the write but the answer's body never ends: cancelled at the body deadline, and the retry is
    accepted as a repeat of the write the backend holds, so the value lands once."""
    marker = run_marker("c")
    arm_fault(card.client, card.page, "stall_body", marker)
    card.edit(marker)
    first, *rest = card.settled_writes(marker, timeout_sec=_BODY_DEADLINE_MS / 1000 + 90)
    cancelled_after = _elapsed(first.headers_at_ms, first.aborted_at_ms)
    assert (first.fault, first.status, first.body_at_ms) == ("stall_body", 200, None), first
    assert _BODY_DEADLINE_MS - 500 <= cancelled_after <= _BODY_DEADLINE_MS + _TIMER_LATE_MS, first
    assert rest and all(write.fault == "pass" for write in rest) and rest[-1].status == 200, rest
    assert rest[0].started_at_ms >= _cancelled_at(first), "the retry started before the cancel"
    log, applied_at = card.log(), card.applied_at_ms(marker)
    attempt_began = log.wall_ms(first.started_at_ms) - _CLOCK_SLACK_MS
    attempt_answered = log.wall_ms(_moment(first.headers_at_ms)) + _CLOCK_SLACK_MS
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
    lost = card.settled_writes(older, timeout_sec=_HEADERS_DEADLINE_MS / 1000 + 90)
    sent = card.settled_writes(newer, timeout_sec=_HEADERS_DEADLINE_MS / 1000 + 90)
    assert len(lost) == 1 and lost[0].status is None, f"the lost edit must not be sent again: {lost}"
    cancelled_at = _cancelled_at(lost[0])
    assert newer_edited_at < cancelled_at, "the newer edit came after the cancel: the stall window was missed"
    assert sent[0].started_at_ms >= cancelled_at, "the newer edit overtook the stalled answer"
    assert sent[-1].status == 200, sent
    assert card.server_reads_after(cancelled_at), "the newer edit never met the version conflict"
    cancelled_wall = card.log().wall_ms(cancelled_at)
    assert card.applied_at_ms(older) <= cancelled_wall + _CLOCK_SLACK_MS, "the older edit must be the write whose answer was lost"
    assert card.applied_at_ms(newer) >= cancelled_wall - _CLOCK_SLACK_MS, "the newer edit must land only after the cancel"
    assert card.stored() == newer and card.shown() == newer
    return {"older_cancelled_after_ms": round(_elapsed(lost[0].started_at_ms, cancelled_at)), "newer_writes": len(sent)}


def _phase_settlement_read_stalls(card: CardSession) -> dict[str, object]:
    """The read that settles a version conflict stalls: it is cancelled at its deadline, the newer edit stays queued, and
    lands once, only after a later read has settled the conflict."""
    older, newer = run_marker("g1"), run_marker("g2")
    arm_fault(card.client, card.page, "hold_headers", older, apply=True)
    card.edit_while_held(older, newer, hold_settlement_read=True)
    patience = 2 * _HEADERS_DEADLINE_MS / 1000 + 90
    lost = card.settled_writes(older, timeout_sec=patience)
    sent = card.settled_writes(newer, timeout_sec=patience)
    assert len(lost) == 1 and lost[0].status is None, f"the lost edit must not be sent again: {lost}"
    older_cancelled_at = _cancelled_at(lost[0])
    reads = card.server_reads_after(older_cancelled_at)
    assert reads, "the newer edit never met the version conflict"
    stalled, *later = reads
    stalled_after = _elapsed(stalled.started_at_ms, stalled.aborted_at_ms)
    assert (stalled.fault, stalled.status) == ("hold_headers", None), reads
    assert _HEADERS_DEADLINE_MS - 500 <= stalled_after <= _HEADERS_DEADLINE_MS + _TIMER_LATE_MS, stalled
    assert later and all(read.fault == "pass" for read in later) and later[-1].status == 200, reads
    settled_read = later[-1]
    assert settled_read.started_at_ms >= _cancelled_at(stalled), "the next read started before the stalled one was cancelled"
    answered_at = _moment(settled_read.headers_at_ms)
    assert sent[-1].status == 200 and sent[-1].started_at_ms >= answered_at, (
        "the edit was re-sent before the conflict was settled"
    )
    log = card.log()
    assert card.applied_at_ms(older) <= log.wall_ms(older_cancelled_at) + _CLOCK_SLACK_MS, (
        "the older edit must be the write whose answer was lost"
    )
    assert card.applied_at_ms(newer) >= log.wall_ms(answered_at) - _CLOCK_SLACK_MS, (
        "the newer edit landed before the conflict was settled"
    )
    assert card.stored() == newer and card.shown() == newer
    return {"read_cancelled_after_ms": round(stalled_after), "reads": len(reads), "newer_writes": len(sent)}


def _run_phase(card: CardSession, name: str, phase: Callable[[], dict[str, object]]) -> dict[str, object]:
    """Runs one phase and adds how long it took; a failure carries every config request the page made, which is what
    tells a cancel from a hang."""
    started = time.monotonic()
    try:
        return {**phase(), "took_s": round(time.monotonic() - started)}
    except AssertionError as error:
        requests = "\n".join(request.describe() for request in card.log().requests)
        raise AssertionError(f"phase {name!r}: {error}\nconfig requests of the page:\n{requests}") from error


def _phase_reopen(api_base: str) -> dict[str, object]:
    """A fresh page has no memory of the session: it loads the configs from the backend, and its first edit lands once.

    The backend's `enabled` flag is flipped first; the card's switch can only show it if the page's read path delivered
    the stored record before the card mounted.
    """
    put_config_value(CONFIG_KEY, {**fetch_config_value(CONFIG_KEY, api_url=api_base), "enabled": True}, api_url=api_base)
    with open_settings_subroute(SETTINGS_ROUTE, timeout_ms=90_000) as (client, page):
        card = CardSession.open(client, page, api_base)
        switch = card.enabled_switch()
        assert switch == "true", f"the reopened card shows the enabled switch as {switch!r} while the backend holds it on"
        install_fault_shim(client, page)
        return _phase_healthy(card, run_marker("f"))


@pytest.mark.chrome_e2e(
    execution_mode="PRIVATE",
    access_scope="GLOBAL_WRITE",
    workload="STANDARD",
    private_reason="global_write_non_namespace",
)
@pytest.mark.integration
def test_config_sync_cancels_stalled_answers_at_deadline_and_keeps_edits_moving() -> None:
    api_base = get_e2e_api_url()
    if not wait_e2e_provider_ready(api_url=api_base):
        pytest.fail("E2E backend not ready for the config-sync deadline signoff")

    backup = fetch_config_value(CONFIG_KEY, api_url=api_base)
    evidence: dict[str, dict[str, object]] = {}
    failures: list[str] = []
    try:
        put_config_value(CONFIG_KEY, _DEFAULTS, api_url=api_base)
        warm_ui_route(SETTINGS_ROUTE)
        with open_settings_subroute(SETTINGS_ROUTE, timeout_ms=90_000) as (client, page):
            card = CardSession.open(client, page, api_base)
            install_fault_shim(client, page)

            phases: dict[str, Callable[[], dict[str, object]]] = {
                "healthy": lambda: _phase_healthy(card, run_marker("a")),
                "slow_headers": lambda: _phase_slow_headers(card),
                "slow_body": lambda: _phase_slow_body(card),
                "stalled_headers": lambda: _phase_stalled_headers(card),
                "stalled_body": lambda: _phase_stalled_body(card),
                "edit_during_stall": lambda: _phase_edit_during_stall(card),
                "settlement_read_stalls": lambda: _phase_settlement_read_stalls(card),
                "healthy_again": lambda: _phase_healthy(card, run_marker("e")),
            }
            for name, phase in phases.items():
                try:
                    evidence[name] = _run_phase(card, name, phase)
                except AssertionError as error:
                    failures.append(str(error))

        try:
            evidence["reopen"] = _phase_reopen(api_base)
        except AssertionError as error:
            failures.append(f"phase 'reopen': {error}")
        print(f"E2E_CONFIG_SYNC_DEADLINE: {json.dumps(evidence)}", flush=True)
        assert not failures, "\n\n".join(failures)
    finally:
        if backup:
            put_config_value(CONFIG_KEY, backup, api_url=api_base)
