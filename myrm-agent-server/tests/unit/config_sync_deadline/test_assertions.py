"""Unit tests: the shared readings refuse a moment that never happened, and one answered write means exactly that."""

from __future__ import annotations

from typing import cast

import pytest

from tests.e2e.config_sync_deadline.assertions import cancelled_at, elapsed, moment, one_answered_write
from tests.e2e.config_sync_deadline.card_session import CardSession
from tests.e2e.config_sync_deadline.fault_shim import ConfigRequest

_MARKER = "jina-e2e-a"


def _write(*, fault: str = "pass", status: int | None = 200, aborted: float | None = None) -> ConfigRequest:
    return ConfigRequest("POST", "/api/v1/config/sync", fault, (_MARKER,), 100.0, 120.0, None, status, aborted)


class _Card:
    """A card whose edit is followed by the given writes and the given number of audit log rows."""

    def __init__(self, writes: list[ConfigRequest], applied: int) -> None:
        self.writes = writes
        self.applied = applied
        self.events: list[str] = []

    def edit(self, value: str) -> None:
        self.events.append(f"edit {value}")

    def wait_stored(self, expected: str) -> None:
        self.events.append(f"stored {expected}")

    def settled_writes(self, marker: str) -> list[ConfigRequest]:
        self.events.append(f"settled {marker}")
        return self.writes

    def applied_count(self, marker: str) -> int:
        self.events.append(f"applied {marker}")
        return self.applied


def test_a_moment_the_request_never_reached_is_refused_and_one_it_reached_is_returned() -> None:
    assert moment(12.5) == 12.5
    with pytest.raises(AssertionError, match="never reached the point"):
        moment(None)


def test_the_time_between_two_moments_needs_both_of_them() -> None:
    assert elapsed(100.0, 10_100.0) == 10_000.0
    with pytest.raises(AssertionError):
        elapsed(None, 5.0)
    with pytest.raises(AssertionError):
        elapsed(5.0, None)


def test_a_cancel_is_the_moment_the_app_aborted_the_request() -> None:
    assert cancelled_at(_write(status=None, aborted=10_100.0)) == 10_100.0
    with pytest.raises(AssertionError, match="never cancelled"):
        cancelled_at(_write())


def test_one_edit_that_became_one_answered_write_is_followed_from_keyboard_to_database() -> None:
    card = _Card([_write()], applied=1)

    assert one_answered_write(cast(CardSession, card), _MARKER) == {"writes": 1}
    assert card.events == [f"edit {_MARKER}", f"stored {_MARKER}", f"settled {_MARKER}", f"applied {_MARKER}"]


@pytest.mark.parametrize(
    "writes",
    [
        [_write(), _write()],
        [_write(fault="slow_headers")],
        [_write(status=500)],
        [_write(status=None, aborted=10_100.0)],
    ],
    ids=["sent twice", "faulted", "not answered with 200", "cancelled"],
)
def test_an_edit_that_was_not_one_plain_answered_write_fails(writes: list[ConfigRequest]) -> None:
    with pytest.raises(AssertionError):
        one_answered_write(cast(CardSession, _Card(writes, applied=1)), _MARKER)


def test_an_edit_the_database_applied_twice_fails() -> None:
    with pytest.raises(AssertionError, match="exactly|once"):
        one_answered_write(cast(CardSession, _Card([_write()], applied=2)), _MARKER)
