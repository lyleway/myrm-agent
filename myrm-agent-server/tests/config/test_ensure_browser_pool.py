"""``ensure_browser_pool`` is the one way the global browser pool comes to exist, and it builds the pool complete.

The harness singleton keeps the arguments of its first caller and ignores every later one. Were the pool built by
several callers, each with part of the deployment's recipe, the pool a process got would depend on which of them ran
first. The real singleton and the real builder run here; only the pool class is replaced, by a double that records how
it was built.
"""

from __future__ import annotations

import asyncio

import pytest
from myrm_agent_harness.toolkits.browser.pool import peek_global_browser_pool, singleton

from app.config.browser import (
    ensure_browser_pool,
    get_browser_launch_options,
    get_browser_pool_config,
)
from app.config.settings import settings
from tests.support.browser_pool_build import (
    CLOUD_ENDPOINT,
    PoolBuild,
    PoolDouble,
    as_double,
    config_of,
    install_pool_build,
)

_CALLERS = 200


@pytest.fixture
def build(monkeypatch: pytest.MonkeyPatch) -> PoolBuild:
    return install_pool_build(monkeypatch)


async def test_the_pool_is_built_with_the_complete_recipe(build: PoolBuild) -> None:
    kwargs = as_double(await ensure_browser_pool()).kwargs

    assert kwargs["max_browsers"] == settings.browser_pool.max_browsers
    assert kwargs["config"] == get_browser_pool_config(remote_ws_endpoint=CLOUD_ENDPOINT)
    assert kwargs["launch_options"] == get_browser_launch_options()
    assert kwargs["extension_bridge"] is build.bridge
    assert kwargs["proxy_pool"] is build.proxies
    assert kwargs["egress_guard"] is build.guard


async def test_the_operators_certificate_choice_reaches_the_pool(build: PoolBuild, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MYRM_BROWSER_IGNORE_HTTPS_ERRORS", "true")

    pool = as_double(await ensure_browser_pool())

    assert config_of(pool).ignore_https_errors is True


async def test_a_pool_that_exists_is_returned_without_resolving_anything_again(build: PoolBuild) -> None:
    first = await ensure_browser_pool()
    second = await ensure_browser_pool()

    assert second is first
    build.constructor.assert_called_once()
    build.cloud_endpoint.assert_awaited_once()
    build.proxy_pool.assert_awaited_once()
    build.egress_guard.assert_called_once()


async def test_a_pool_built_elsewhere_is_never_replaced(build: PoolBuild, monkeypatch: pytest.MonkeyPatch) -> None:
    existing = PoolDouble(origin="elsewhere")
    monkeypatch.setattr(singleton, "_global_pool", existing)

    assert await ensure_browser_pool() is existing

    build.constructor.assert_not_called()
    build.cloud_endpoint.assert_not_awaited()
    build.proxy_pool.assert_not_awaited()
    existing.shutdown.assert_not_awaited()


async def test_concurrent_callers_get_the_one_pool_a_single_build_made(build: PoolBuild) -> None:
    pools = await asyncio.gather(*(ensure_browser_pool() for _ in range(_CALLERS)))

    assert all(pool is pools[0] for pool in pools)
    build.constructor.assert_called_once()
    build.cloud_endpoint.assert_awaited_once()
    build.proxy_pool.assert_awaited_once()
    build.egress_guard.assert_called_once()


async def test_a_failed_build_leaves_no_pool_and_the_next_caller_builds_one(build: PoolBuild) -> None:
    pool = PoolDouble()
    build.constructor.side_effect = [RuntimeError("cannot build the pool"), pool]

    with pytest.raises(RuntimeError, match="cannot build the pool"):
        await ensure_browser_pool()
    assert peek_global_browser_pool() is None

    assert await ensure_browser_pool() is pool
