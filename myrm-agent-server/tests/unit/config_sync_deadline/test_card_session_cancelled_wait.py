"""Unit tests: the card session waits until the app has given up on enough attempts of one edit, and says so when it never does."""

from __future__ import annotations

from typing import cast

import pytest

from tests.e2e.config_sync_deadline import card_session
from tests.e2e.config_sync_deadline.card_session import CardSession
from tests.e2e.config_sync_deadline.fault_shim import ConfigRequest
from tests.support.chrome_mcp_e2e import ChromeMcpClient, McpPage

_MARKER = "jina-e2e-a1"


def _attempt(*, aborted: float | None) -> ConfigRequest:
    return ConfigRequest("POST", "/api/v1/config/sync", "pass", (_MARKER,), 100.0, None, None, None, aborted)


class _Clock:
    """Stands in for ``time``: sleeping moves the clock, so polling loops end at once."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr(card_session, "time", fake)
    return fake


@pytest.fixture
def progress(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    nodes: list[str] = []
    monkeypatch.setattr(card_session, "touch_wall_progress", lambda *, current_node: nodes.append(current_node))
    return nodes


def _session_polling(monkeypatch: pytest.MonkeyPatch, *polls: list[ConfigRequest]) -> CardSession:
    """A card whose page log shows one list of the marker's writes per poll, the last one for every poll after."""
    pending = list(polls)

    def writes(self: CardSession, marker: str) -> list[ConfigRequest]:
        assert marker == _MARKER
        return pending.pop(0) if len(pending) > 1 else pending[0]

    monkeypatch.setattr(CardSession, "writes", writes)
    return CardSession(cast(ChromeMcpClient, object()), cast(McpPage, object()), "http://api.test")


def test_the_wait_ends_with_the_cancelled_writes_as_soon_as_there_are_enough(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, progress: list[str]
) -> None:
    first, second = _attempt(aborted=10_100.0), _attempt(aborted=20_300.0)
    session = _session_polling(
        monkeypatch,
        [_attempt(aborted=None)],
        [first, _attempt(aborted=None)],
        [first, second, _attempt(aborted=None)],
    )

    assert session.wait_for_cancelled_writes(_MARKER, 2) == [first, second]
    assert progress == ["config_sync_wait_cancelled"] * 3
    assert clock.now == 1.0


def test_the_wait_fails_with_the_writes_it_saw_when_the_app_never_gives_up(
    monkeypatch: pytest.MonkeyPatch, clock: _Clock, progress: list[str]
) -> None:
    session = _session_polling(monkeypatch, [_attempt(aborted=None)])

    with pytest.raises(AssertionError, match=rf"cancelled fewer than 1 writes of '{_MARKER}' within 2.0s"):
        session.wait_for_cancelled_writes(_MARKER, 1, timeout_sec=2.0)

    assert clock.now >= 2.0
    assert progress and set(progress) == {"config_sync_wait_cancelled"}
