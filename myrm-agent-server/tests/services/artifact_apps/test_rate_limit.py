"""The fetch proxy bounds one app's burst, not its lifetime usage."""

from __future__ import annotations

from app.services.artifact_apps.rate_limit import CallLimiter


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_calls_over_the_per_minute_budget_are_refused_until_the_window_moves_on() -> None:
    clock = _Clock()
    limiter = CallLimiter(per_minute=3, max_in_flight=10, clock=clock)

    for _ in range(3):
        assert limiter.try_acquire("app")
        limiter.release("app")
    assert not limiter.try_acquire("app")

    clock.now += 60.0
    assert limiter.try_acquire("app")


def test_calls_beyond_the_in_flight_cap_are_refused_until_one_finishes() -> None:
    limiter = CallLimiter(per_minute=100, max_in_flight=2, clock=_Clock())

    assert limiter.try_acquire("app")
    assert limiter.try_acquire("app")
    assert not limiter.try_acquire("app")

    limiter.release("app")
    assert limiter.try_acquire("app")


def test_each_app_has_its_own_budget() -> None:
    limiter = CallLimiter(per_minute=1, max_in_flight=1, clock=_Clock())

    assert limiter.try_acquire("a")
    assert limiter.try_acquire("b")
    assert not limiter.try_acquire("a")


def test_release_never_drives_the_in_flight_count_negative() -> None:
    limiter = CallLimiter(per_minute=100, max_in_flight=1, clock=_Clock())

    limiter.release("app")
    assert limiter.try_acquire("app")
    assert not limiter.try_acquire("app")
