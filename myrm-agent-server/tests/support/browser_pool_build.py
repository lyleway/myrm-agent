"""Build the global browser pool through the real singleton and the real builder, without a browser.

[INPUT]
- myrm_agent_harness.toolkits.browser.pool.singleton (POS: the process singleton; its first caller's arguments are the pool's configuration)
- app.config.browser::ensure_browser_pool and the resolvers it composes (POS: the one builder of the pool)

[OUTPUT]
- PoolDouble: records the arguments the pool was constructed with and the calls made on a running pool
- as_double: the pool a build returned, known to be the double
- config_of: the BrowserPoolConfig the pool double was constructed with
- PoolBuild: the seams of one build (the constructor and each resolved ingredient)
- install_pool_build: start from no pool and make every ingredient resolve after yielding to the event loop

[POS]
Server pytest-only helper shared by the tests of the builder, the lifecycle, the configuration hot reload and the
health endpoint. Only the pool class is replaced; the singleton, the builder and its lock are the real ones, so a test
fails when two callers could end up with different pools.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock

import pytest
from myrm_agent_harness.toolkits.browser.pool import singleton
from myrm_agent_harness.toolkits.browser.pool.config import BrowserPoolConfig

import app.config.browser as browser_config

CLOUD_ENDPOINT = "wss://cloud.example/cdp"


class PoolDouble:
    """What the pool class was constructed with, and the calls made on a running pool."""

    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.shutdown = AsyncMock()
        self.warmup = AsyncMock()
        self.update_proxy_pool = AsyncMock()
        self.update_remote_endpoint = AsyncMock()
        self.health = AsyncMock(return_value={"status": "healthy"})


def as_double(pool: object) -> PoolDouble:
    """The pool a build returned, known to be the double (the builder is typed to return the real class)."""
    assert isinstance(pool, PoolDouble), f"not a pool double: {pool!r}"
    return pool


def config_of(pool: PoolDouble) -> BrowserPoolConfig:
    """The configuration the pool double was constructed with."""
    config = pool.kwargs["config"]
    assert isinstance(config, BrowserPoolConfig)
    return config


@dataclass(frozen=True)
class PoolBuild:
    """The seams of one build: the pool class, and each ingredient of the recipe with the value it resolves to."""

    constructor: MagicMock
    cloud_endpoint: AsyncMock
    proxy_pool: AsyncMock
    egress_guard: MagicMock
    proxies: object
    guard: object
    bridge: object


def install_pool_build(monkeypatch: pytest.MonkeyPatch) -> PoolBuild:
    """Start from no pool; every ingredient yields to the event loop, as the database reads do."""
    proxies, guard, bridge = object(), object(), object()

    async def resolve_cloud_endpoint() -> str:
        await asyncio.sleep(0)
        return CLOUD_ENDPOINT

    async def resolve_proxy_pool() -> object:
        await asyncio.sleep(0)
        return proxies

    build = PoolBuild(
        constructor=MagicMock(side_effect=PoolDouble),
        cloud_endpoint=AsyncMock(side_effect=resolve_cloud_endpoint),
        proxy_pool=AsyncMock(side_effect=resolve_proxy_pool),
        egress_guard=MagicMock(return_value=guard),
        proxies=proxies,
        guard=guard,
        bridge=bridge,
    )
    monkeypatch.setattr(singleton, "_global_pool", None)
    monkeypatch.setattr(singleton, "GlobalBrowserPool", build.constructor)
    # A lock that was contended in one test is bound to that test's event loop.
    monkeypatch.setattr(browser_config, "_creation_lock", asyncio.Lock())
    monkeypatch.setattr(browser_config, "is_local_mode", lambda: False)
    monkeypatch.delenv("MYRM_BROWSER_IGNORE_HTTPS_ERRORS", raising=False)
    monkeypatch.setattr(browser_config, "resolve_cloud_browser_endpoint", build.cloud_endpoint)
    monkeypatch.setattr(browser_config, "resolve_browser_proxy_pool", build.proxy_pool)
    monkeypatch.setattr(browser_config, "get_browser_egress_guard", build.egress_guard)
    monkeypatch.setattr("app.services.extension.bridge.get_extension_bridge", lambda: bridge)
    return build
