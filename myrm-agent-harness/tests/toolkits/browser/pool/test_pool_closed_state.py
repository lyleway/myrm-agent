"""A pool that was shut down launches nothing and hands out no page, even to a caller that still holds it.

A session that outlives the application's shutdown, or an acquisition that was already queued on the pool lock, used
to bring a browser back: the pool launched a new one and restarted its lifecycle loop, and nothing closed them again.
Only the browsers are doubles: pool, lock, semaphore, page pools and the lifecycle loop are the real ones.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from myrm_agent_harness.toolkits.browser.exceptions import BrowserLaunchError, BrowserPoolClosedError, BrowserPoolError
from myrm_agent_harness.toolkits.browser.pool import ContextType, GlobalBrowserPool
from myrm_agent_harness.toolkits.browser.pool.browser_launcher import BrowserInstance
from myrm_agent_harness.toolkits.browser.pool.config import BrowserEngine, BrowserPoolConfig, LaunchMode
from myrm_agent_harness.toolkits.browser.pool.page_pool import PagePool
from myrm_agent_harness.toolkits.browser.utils.page_health import is_driver_closed_error
from tests.toolkits.browser._fake_pages import FakeCdp, FakePage

if TYPE_CHECKING:
    from patchright.async_api import Page

_LIMIT = 4
_KEY = "ctx"
_BOUND_S = 2.0
_PAGE_GONE = "BrowserContext.new_page: Connection closed while reading from the driver"


def _instance(*, new_page: AsyncMock) -> BrowserInstance:
    context = MagicMock(close=AsyncMock())
    context.new_page = new_page
    browser = MagicMock(close=AsyncMock())
    browser.new_browser_cdp_session = AsyncMock(
        return_value=MagicMock(send=AsyncMock(return_value={}), detach=AsyncMock())
    )
    return BrowserInstance(
        browser=browser,
        contexts={_KEY: context},
        page_pools={_KEY: PagePool(context, max_size=_LIMIT)},
    )


def _live_instance() -> BrowserInstance:
    return _instance(new_page=AsyncMock(side_effect=lambda: FakePage(cdp=FakeCdp())))


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(_BOUND_S):
        while not condition():
            await asyncio.sleep(0)


@dataclass
class Rig:
    pool: GlobalBrowserPool
    launched: list[BrowserInstance] = field(default_factory=list)

    async def least_loaded(self, _engine: BrowserEngine, _mode: LaunchMode | None) -> BrowserInstance:
        """The pool's scheduling contract: use the browser that is there, launch one when the pool is empty."""
        if not self.pool._browsers:
            fresh = _live_instance()
            self.launched.append(fresh)
            self.pool._browsers.append(fresh)
        return self.pool._browsers[0]

    async def take(self) -> tuple[Page, str]:
        return await self.pool.acquire_page(ContextType.AGENT, context_key=_KEY)

    async def warm(self, browsers: int) -> None:
        await self.pool.warmup(browsers=browsers)

    def assert_nothing_was_taken(self) -> None:
        assert self.pool._global_semaphore._value == _LIMIT, "permit leaked or over-released"
        assert self.pool._current_pages_in_use == 0
        assert self.pool._total_acquires == 0


@pytest.fixture
async def rig(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Rig]:
    pool = GlobalBrowserPool(max_browsers=1, config=BrowserPoolConfig(max_concurrent_pages=_LIMIT))
    rig = Rig(pool)
    monkeypatch.setattr(pool, "_get_least_loaded_browser", rig.least_loaded)
    monkeypatch.setattr(pool._memory_guard, "check_memory", AsyncMock())
    yield rig
    await asyncio.wait_for(pool.shutdown(), timeout=_BOUND_S)  # ends the lifecycle loop a test may have started


def test_the_closed_pool_error_is_a_pool_error_that_no_retry_mistakes_for_something_else() -> None:
    error = BrowserPoolClosedError("The browser pool was shut down")

    assert isinstance(error, BrowserPoolError)
    assert not isinstance(error, BrowserLaunchError), "launch failures are retried; a closed pool never is"
    assert not is_driver_closed_error(error)


async def test_a_closed_pool_refuses_an_acquisition_and_launches_nothing(rig: Rig) -> None:
    await rig.pool.shutdown()

    with pytest.raises(BrowserPoolClosedError):
        await rig.take()

    assert not rig.launched
    assert rig.pool._lifecycle_task is None
    rig.assert_nothing_was_taken()


async def test_a_session_that_outlives_the_pool_does_not_bring_the_browser_back(rig: Rig) -> None:
    page, key = await rig.take()
    await rig.pool.release_page(page, key)
    assert len(rig.launched) == 1
    assert rig.pool._lifecycle_task is not None, "a working pool runs its lifecycle loop"

    await rig.pool.shutdown()
    with pytest.raises(BrowserPoolClosedError):
        await rig.take()

    assert len(rig.launched) == 1, "no second browser once the first was closed"
    assert rig.pool._browsers == []
    assert rig.pool._lifecycle_task is None, "a late acquisition must not restart the loop"


async def test_the_lifecycle_loop_is_not_started_on_a_closed_pool(rig: Rig) -> None:
    await rig.pool.shutdown()

    rig.pool._ensure_lifecycle_loop()

    assert rig.pool._lifecycle_task is None


async def test_a_closed_pool_refuses_fast_when_every_permit_is_taken(rig: Rig) -> None:
    """A closed pool never gets its permits back, so the refusal must come before the wait for one."""
    for _ in range(_LIMIT):
        await rig.pool._global_semaphore.acquire()
    await rig.pool.shutdown()

    with pytest.raises(BrowserPoolClosedError):
        await asyncio.wait_for(rig.take(), timeout=_BOUND_S)


async def test_an_acquisition_queued_on_the_pool_lock_is_refused_once_shutdown_ran(rig: Rig) -> None:
    """`shutdown` closes the browsers under the pool lock; whoever queued before it must not launch afterwards."""
    await rig.pool._lock.acquire()
    try:
        queued = asyncio.ensure_future(rig.take())
        await _until(lambda: rig.pool._global_semaphore._value == _LIMIT - 1)  # it holds a permit, waits for the lock
        closing = asyncio.ensure_future(rig.pool.shutdown())
        await _until(lambda: rig.pool._closed)
    finally:
        rig.pool._lock.release()

    with pytest.raises(BrowserPoolClosedError):
        await asyncio.wait_for(queued, timeout=_BOUND_S)
    await asyncio.wait_for(closing, timeout=_BOUND_S)

    assert not rig.launched
    assert rig.pool._browsers == []
    assert rig.pool._global_semaphore._value == _LIMIT, "the refused acquisition returned its permit"


async def test_the_retry_after_a_driver_exit_ends_at_the_closed_pool_instead_of_launching_a_replacement(
    rig: Rig, monkeypatch: pytest.MonkeyPatch
) -> None:
    rig.pool._browsers.append(_instance(new_page=AsyncMock(side_effect=Exception(_PAGE_GONE))))
    evict = rig.pool._evict_after_driver_exit

    async def evict_then_shut_down() -> None:
        await evict()
        await rig.pool.shutdown()

    monkeypatch.setattr(rig.pool, "_evict_after_driver_exit", evict_then_shut_down)

    with pytest.raises(BrowserPoolClosedError):
        await rig.take()

    assert not rig.launched
    assert rig.pool._global_semaphore._value == _LIMIT


async def test_a_closed_pool_refuses_warmup_and_creates_no_launcher(rig: Rig) -> None:
    await rig.pool.shutdown()

    for browsers in (2, 0):
        with pytest.raises(BrowserPoolClosedError):
            await rig.warm(browsers)

    assert rig.pool._launchers == {}


async def test_a_session_that_outlives_the_pool_can_still_let_go_of_its_page(rig: Rig) -> None:
    page, key = await rig.take()
    await rig.pool.shutdown()

    await rig.pool.release_page(page, key)

    assert await rig.pool.destroy_context(key) is False


async def test_shutdown_is_idempotent(rig: Rig) -> None:
    await rig.pool.shutdown()
    await rig.pool.shutdown()

    with pytest.raises(BrowserPoolClosedError):
        await rig.take()
