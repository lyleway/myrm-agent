"""Real-Chromium proof that a pool that was shut down stays shut down for a caller that still holds it.

Without the closed state, the acquisition of a session that outlives the shutdown launches a new Chromium and restarts
the lifecycle loop, and nothing closes them again. The browsers here are real and are counted in the process table.
Run with: `pytest -m integration tests/toolkits/browser/pool/test_closed_pool_real_browser.py`.
"""

from __future__ import annotations

import asyncio
import contextlib

import psutil
import pytest

from myrm_agent_harness.toolkits.browser.exceptions import BrowserPoolClosedError
from myrm_agent_harness.toolkits.browser.pool import (
    ContextType,
    GlobalBrowserPool,
    get_global_browser_pool,
    shutdown_global_browser_pool,
)
from tests.toolkits.browser._browser_available import chromium_available
from tests.toolkits.browser._launched_processes import browser_main_processes, driver_processes

pytestmark = [
    pytest.mark.integration,
    pytest.mark.slow,
    pytest.mark.asyncio,
    pytest.mark.skipif(not chromium_available(), reason="Chromium not available"),
]

_BOUND_S = 90.0
_TEARDOWN_TIMEOUT_S = 20.0
_ENDED_TIMEOUT_S = 20.0
_SEEN_TIMEOUT_S = 15.0
_QUIET_SAMPLES = 8
_QUIET_SAMPLE_INTERVAL_S = 0.25


async def _take_page(pool: GlobalBrowserPool) -> None:
    page, key = await pool.acquire_page(ContextType.CRAWL)
    await pool.release_page(page, key)


async def _until_none_runs(pids: set[int]) -> None:
    async with asyncio.timeout(_ENDED_TIMEOUT_S):
        while pids & set(browser_main_processes()):
            await asyncio.sleep(0.2)


async def _launched_since(before: set[int]) -> set[int]:
    """The browsers launched since `before`; the process table is read process by process, so a launch may take a moment to show."""
    async with asyncio.timeout(_SEEN_TIMEOUT_S):
        while not (launched := set(browser_main_processes()) - before):
            await asyncio.sleep(0.2)
    return launched


async def test_a_late_acquisition_after_shutdown_launches_no_chromium() -> None:
    # The default launch mode starts a Chromium of the pool's own, never the user's browser.
    pool = GlobalBrowserPool(max_browsers=1)
    browsers_before = set(browser_main_processes())
    drivers_before = set(driver_processes())
    try:
        async with asyncio.timeout(_BOUND_S):
            await _take_page(pool)
            launched = await _launched_since(browsers_before)

            await pool.shutdown()
            await _until_none_runs(launched)

            with pytest.raises(BrowserPoolClosedError):
                await _take_page(pool)
            for _ in range(_QUIET_SAMPLES):  # a revived browser stays up, so any one reading that sees it is enough
                assert set(browser_main_processes()) <= browsers_before, "a Chromium was launched after the shutdown"
                assert set(driver_processes()) <= drivers_before, "a Playwright driver was started after the shutdown"
                await asyncio.sleep(_QUIET_SAMPLE_INTERVAL_S)
            assert pool._browsers == []
            assert pool._lifecycle_task is None
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(pool.shutdown(), timeout=_TEARDOWN_TIMEOUT_S)
        for pid, proc in browser_main_processes().items():
            if pid not in browsers_before:
                with contextlib.suppress(psutil.Error):
                    proc.kill()


async def test_the_next_first_caller_starts_a_working_pool_after_the_singleton_was_shut_down() -> None:
    browsers_before = set(browser_main_processes())
    try:
        async with asyncio.timeout(_BOUND_S):
            first = get_global_browser_pool(max_browsers=1)
            await _take_page(first)

            await shutdown_global_browser_pool()
            second = get_global_browser_pool(max_browsers=1)
            await _take_page(second)

            assert second is not first
            with pytest.raises(BrowserPoolClosedError):
                await _take_page(first)
    finally:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(shutdown_global_browser_pool(), timeout=_TEARDOWN_TIMEOUT_S)
        for pid, proc in browser_main_processes().items():
            if pid not in browsers_before:
                with contextlib.suppress(psutil.Error):
                    proc.kill()
