"""Saving the cloud browser or proxy settings reconfigures the running pool and never builds one.

A hot reload that asked for the pool would build one with part of the recipe when none exists yet (no proxy, no
extension bridge) and make it the process's pool. A pool that does not exist yet reads the saved settings when it is
built, so there is nothing to reload; a pool that was built before the settings were readable is completed by it, and
a save that lands while a pool is being built waits for the build and reaches the pool it produced.
"""

from __future__ import annotations

import asyncio

import pytest
from myrm_agent_harness.toolkits.browser.pool import peek_global_browser_pool
from myrm_agent_harness.toolkits.browser.pool.proxy import RoundRobinProxyPool

from app.api.config.routes.support import _hot_reload_browser_proxy, _hot_reload_cloud_browser
from app.config.browser import ensure_browser_pool
from tests.support.browser_pool_build import PoolBuild, as_double, install_pool_build

_CLOUD = {"enabled": True, "provider": "custom", "credential": "key", "custom_ws_url": "wss://saved.example/cdp"}
_CLOUD_OFF = {"enabled": False}
_PROXIES = ["http://saved-one.example:8080", "http://saved-two.example:8080"]
_BOUND_S = 5.0


@pytest.fixture
def build(monkeypatch: pytest.MonkeyPatch) -> PoolBuild:
    return install_pool_build(monkeypatch)


async def test_without_a_pool_nothing_is_built_and_nothing_is_reloaded(build: PoolBuild) -> None:
    await _hot_reload_cloud_browser(_CLOUD)
    await _hot_reload_browser_proxy({"enabled": True, "proxies": _PROXIES})

    build.constructor.assert_not_called()
    assert peek_global_browser_pool() is None
    build.cloud_endpoint.assert_not_awaited()
    build.proxy_pool.assert_not_awaited()


async def test_the_running_pool_takes_the_saved_cloud_endpoint(build: PoolBuild) -> None:
    pool = as_double(await ensure_browser_pool())

    await _hot_reload_cloud_browser(_CLOUD)

    pool.update_remote_endpoint.assert_awaited_once_with("wss://saved.example/cdp")


async def test_switching_the_cloud_browser_off_clears_the_endpoint_of_the_running_pool(build: PoolBuild) -> None:
    pool = as_double(await ensure_browser_pool())

    await _hot_reload_cloud_browser(_CLOUD_OFF)

    pool.update_remote_endpoint.assert_awaited_once_with(None)


async def test_the_running_pool_takes_the_saved_proxies(build: PoolBuild) -> None:
    pool = as_double(await ensure_browser_pool())

    await _hot_reload_browser_proxy({"enabled": True, "proxies": _PROXIES})

    pool.update_proxy_pool.assert_awaited_once()
    call = pool.update_proxy_pool.await_args
    assert call is not None
    (proxy_pool,) = call.args
    assert isinstance(proxy_pool, RoundRobinProxyPool)
    assert proxy_pool.urls == _PROXIES


async def test_switching_the_proxies_off_clears_the_proxy_pool_of_the_running_pool(build: PoolBuild) -> None:
    pool = as_double(await ensure_browser_pool())

    await _hot_reload_browser_proxy({"enabled": False, "proxies": _PROXIES})

    pool.update_proxy_pool.assert_awaited_once_with(None)


async def test_a_pool_built_before_the_settings_were_readable_is_completed_in_place(build: PoolBuild) -> None:
    """The first build found no saved settings (first run, database not ready); the user saves them afterwards."""
    build.cloud_endpoint.side_effect = None
    build.cloud_endpoint.return_value = None
    build.proxy_pool.side_effect = None
    build.proxy_pool.return_value = None
    pool = as_double(await ensure_browser_pool())
    assert pool.kwargs["proxy_pool"] is None

    await _hot_reload_cloud_browser(_CLOUD)
    await _hot_reload_browser_proxy({"enabled": True, "proxies": _PROXIES})

    assert peek_global_browser_pool() is pool
    build.constructor.assert_called_once()
    pool.shutdown.assert_not_awaited()
    pool.update_remote_endpoint.assert_awaited_once_with("wss://saved.example/cdp")
    pool.update_proxy_pool.assert_awaited_once()


async def test_a_save_that_lands_while_the_pool_is_being_built_reaches_the_pool_the_build_produced(
    build: PoolBuild,
) -> None:
    """The build read its recipe before the save, so a reload that saw no pool yet would lose the saved setting."""
    reading = asyncio.Event()
    release = asyncio.Event()

    async def read_the_recipe_before_the_save() -> None:
        reading.set()
        await release.wait()

    build.cloud_endpoint.side_effect = read_the_recipe_before_the_save
    building = asyncio.create_task(ensure_browser_pool())
    reloading: asyncio.Task[None] | None = None
    try:
        async with asyncio.timeout(_BOUND_S):
            await reading.wait()
        reloading = asyncio.create_task(_hot_reload_cloud_browser(_CLOUD))
        for _ in range(5):
            await asyncio.sleep(0)
        assert not reloading.done(), "the reload ran while the build was still reading its recipe"
    finally:
        release.set()
        async with asyncio.timeout(_BOUND_S):
            await asyncio.gather(building, *([reloading] if reloading else []))

    pool = as_double(building.result())
    assert peek_global_browser_pool() is pool
    build.constructor.assert_called_once()
    pool.shutdown.assert_not_awaited()
    pool.update_remote_endpoint.assert_awaited_once_with("wss://saved.example/cdp")


async def test_a_value_that_does_not_validate_leaves_the_running_pool_alone(build: PoolBuild) -> None:
    pool = as_double(await ensure_browser_pool())

    await _hot_reload_cloud_browser({"enabled": "maybe"})
    await _hot_reload_browser_proxy({"proxies": "not-a-list"})

    pool.update_remote_endpoint.assert_not_awaited()
    pool.update_proxy_pool.assert_not_awaited()
