"""The application's browser pool is warmed up by the one builder and never replaced under the sessions using it.

Warmup used to close the pool that already existed and build another. A request that arrived during startup had built
the pool it was using, so that closed the browsers under it (its pages died), left the old pool's browser running, and
built the replacement with a recipe that could differ from the first. The real singleton and the real builder run here;
only the pool class is a double.
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest
from myrm_agent_harness.toolkits.browser.pool import peek_global_browser_pool

from app.config.browser import ensure_browser_pool
from app.config.settings import settings
from app.lifecycle.browser import shutdown_global_browser_pool, warmup_global_browser_pool
from tests.support.browser_pool_build import CLOUD_ENDPOINT, PoolBuild, PoolDouble, as_double, config_of, install_pool_build

_REQUESTS = 200
_WARMUPS = 20


@pytest.fixture
def build(monkeypatch: pytest.MonkeyPatch) -> PoolBuild:
    monkeypatch.setattr("app.core.security.browser_vault.get_global_session_vault", lambda: MagicMock())
    monkeypatch.setattr("myrm_agent_harness.toolkits.web_fetch.web_fetch_tools.set_session_vault", MagicMock())
    return install_pool_build(monkeypatch)


def _assert_complete(build: PoolBuild, pool: PoolDouble) -> None:
    """The recipe of the deployment, whichever caller built the pool."""
    assert pool.kwargs["egress_guard"] is build.guard
    assert pool.kwargs["proxy_pool"] is build.proxies
    assert pool.kwargs["extension_bridge"] is build.bridge
    assert config_of(pool).remote_ws_endpoint == CLOUD_ENDPOINT


async def test_warmup_builds_the_pool_with_the_complete_recipe_and_warms_it(build: PoolBuild) -> None:
    await warmup_global_browser_pool()

    pool = as_double(peek_global_browser_pool())
    build.constructor.assert_called_once()
    _assert_complete(build, pool)
    pool.warmup.assert_awaited_once_with(
        browsers=settings.browser_pool.warmup_browsers,
        pages_per_context=settings.browser_pool.warmup_pages,
    )


async def test_warmup_keeps_the_pool_a_request_already_built(build: PoolBuild) -> None:
    in_use = as_double(await ensure_browser_pool())

    await warmup_global_browser_pool()

    assert peek_global_browser_pool() is in_use
    in_use.shutdown.assert_not_awaited()
    in_use.warmup.assert_awaited_once()
    build.constructor.assert_called_once()


async def test_a_request_that_needs_the_pool_while_warmup_builds_it_shares_that_pool(build: PoolBuild) -> None:
    await asyncio.gather(warmup_global_browser_pool(), ensure_browser_pool())

    pool = as_double(peek_global_browser_pool())
    build.constructor.assert_called_once()
    _assert_complete(build, pool)
    pool.shutdown.assert_not_awaited()
    pool.warmup.assert_awaited_once()


async def test_many_requests_and_warmups_at_once_leave_one_complete_pool(build: PoolBuild) -> None:
    results = await asyncio.gather(
        *(ensure_browser_pool() for _ in range(_REQUESTS)),
        *(warmup_global_browser_pool() for _ in range(_WARMUPS)),
    )

    pool = as_double(peek_global_browser_pool())
    assert all(result is pool for result in results[:_REQUESTS])
    build.constructor.assert_called_once()
    _assert_complete(build, pool)
    pool.shutdown.assert_not_awaited()
    assert pool.warmup.await_count == _WARMUPS


async def test_shutdown_closes_the_pool_and_forgets_it(build: PoolBuild) -> None:
    pool = as_double(await ensure_browser_pool())

    await shutdown_global_browser_pool()

    pool.shutdown.assert_awaited_once()
    assert peek_global_browser_pool() is None


async def test_shutdown_without_a_pool_builds_none(build: PoolBuild) -> None:
    await shutdown_global_browser_pool()

    build.constructor.assert_not_called()
    assert peek_global_browser_pool() is None
