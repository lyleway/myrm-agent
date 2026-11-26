"""The global pool singleton: created by its first caller, only peeked at by observers, forgotten by shutdown.

The first caller's configuration is the pool's configuration, so an observer that creates the pool just to look at it
pins whatever configuration it happened to have; `peek_global_browser_pool` never creates. `shutdown_global_browser_pool`
closes the pool and forgets it, and the closed pool refuses work, so a holder of the old instance cannot relaunch
browsers behind the application's back.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from myrm_agent_harness.toolkits.browser.exceptions import BrowserPoolClosedError
from myrm_agent_harness.toolkits.browser.pool import (
    ContextType,
    GlobalBrowserPool,
    get_global_browser_pool,
    peek_global_browser_pool,
    shutdown_global_browser_pool,
    singleton,
)


async def _take_page(pool: GlobalBrowserPool) -> None:
    await pool.acquire_page(ContextType.CRAWL)


@pytest.fixture(autouse=True)
def no_singleton(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every test starts without a global pool; the original is restored afterwards."""
    monkeypatch.setattr(singleton, "_global_pool", None)


@pytest.fixture
def pool_constructor(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Counts constructions; each one yields a pool double whose shutdown is observable."""
    constructor = MagicMock(side_effect=lambda **_kwargs: MagicMock(shutdown=AsyncMock()))
    monkeypatch.setattr(singleton, "GlobalBrowserPool", constructor)
    return constructor


def test_peek_creates_nothing(pool_constructor: MagicMock) -> None:
    assert peek_global_browser_pool() is None
    assert peek_global_browser_pool() is None

    pool_constructor.assert_not_called()


def test_peek_returns_the_pool_the_first_caller_created(pool_constructor: MagicMock) -> None:
    pool = get_global_browser_pool(max_browsers=1)

    assert peek_global_browser_pool() is pool


def test_the_first_caller_decides_the_configuration_and_later_callers_get_the_same_pool(
    pool_constructor: MagicMock,
) -> None:
    first = get_global_browser_pool(max_browsers=1)
    second = get_global_browser_pool(max_browsers=9, proxy_pool=MagicMock())

    assert second is first
    pool_constructor.assert_called_once()
    assert pool_constructor.call_args.kwargs["max_browsers"] == 1
    assert pool_constructor.call_args.kwargs["proxy_pool"] is None


async def test_shutdown_closes_the_pool_and_forgets_it(pool_constructor: MagicMock) -> None:
    pool = get_global_browser_pool(max_browsers=1)

    await shutdown_global_browser_pool()

    pool.shutdown.assert_awaited_once()
    assert peek_global_browser_pool() is None
    assert get_global_browser_pool(max_browsers=1) is not pool, "the next first caller starts a new pool"


async def test_shutdown_without_a_pool_creates_nothing(pool_constructor: MagicMock) -> None:
    await shutdown_global_browser_pool()
    await shutdown_global_browser_pool()

    pool_constructor.assert_not_called()
    assert peek_global_browser_pool() is None


async def test_shutdown_forgets_a_pool_that_does_not_close_in_time(
    pool_constructor: MagicMock, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(singleton, "_SHUTDOWN_TIMEOUT_SEC", 0.05)

    async def never_closes() -> None:
        await asyncio.Event().wait()

    pool = get_global_browser_pool(max_browsers=1)
    pool.shutdown = AsyncMock(side_effect=never_closes)

    with caplog.at_level(logging.WARNING):
        await asyncio.wait_for(shutdown_global_browser_pool(), timeout=2.0)

    assert peek_global_browser_pool() is None
    assert "Global browser pool shutdown timed out" in caplog.text


async def test_a_holder_of_the_old_pool_cannot_relaunch_browsers_after_shutdown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The pool and its scheduling are real; preparing a launcher, the first step of a launch, fails the test."""
    pool = get_global_browser_pool(max_browsers=1)
    assert isinstance(pool, GlobalBrowserPool)
    prepared: list[object] = []
    monkeypatch.setattr(pool, "_get_launcher", lambda *args: prepared.append(args) or MagicMock())

    await shutdown_global_browser_pool()

    with pytest.raises(BrowserPoolClosedError):
        await _take_page(pool)
    assert not prepared, "a refused acquisition must not even prepare a launcher"
    assert pool._browsers == []
    assert pool._lifecycle_task is None
