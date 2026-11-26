"""``resolve_browser_proxy_pool``: the proxies saved in the settings win over the ``MYRM_PROXIES`` environment variable."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config.browser import resolve_browser_proxy_pool
from app.services.config.service import config_service

_SAVED = ["http://saved-one.example:8080", "http://saved-two.example:8080"]
_FROM_ENV = "http://from-env.example:9000"


def _saved(monkeypatch: pytest.MonkeyPatch, *, enabled: bool, proxies: list[str]) -> None:
    record = SimpleNamespace(value={"enabled": enabled, "proxies": proxies})
    monkeypatch.setattr(config_service, "get", AsyncMock(return_value=record))


async def test_enabled_saved_proxies_win_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    _saved(monkeypatch, enabled=True, proxies=_SAVED)
    monkeypatch.setenv("MYRM_PROXIES", _FROM_ENV)

    pool = await resolve_browser_proxy_pool()

    assert pool is not None
    assert pool.urls == _SAVED


@pytest.mark.parametrize(
    ("enabled", "proxies"),
    [(False, _SAVED), (True, [])],
    ids=["disabled", "no-proxies"],
)
async def test_saved_proxies_that_are_switched_off_fall_back_to_the_environment(
    monkeypatch: pytest.MonkeyPatch, enabled: bool, proxies: list[str]
) -> None:
    _saved(monkeypatch, enabled=enabled, proxies=proxies)
    monkeypatch.setenv("MYRM_PROXIES", _FROM_ENV)

    pool = await resolve_browser_proxy_pool()

    assert pool is not None
    assert pool.urls == [_FROM_ENV]


async def test_an_unreadable_configuration_falls_back_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_service, "get", AsyncMock(side_effect=RuntimeError("database not ready")))
    monkeypatch.setenv("MYRM_PROXIES", _FROM_ENV)

    pool = await resolve_browser_proxy_pool()

    assert pool is not None
    assert pool.urls == [_FROM_ENV]


async def test_no_proxies_anywhere_means_no_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config_service, "get", AsyncMock(return_value=None))
    monkeypatch.delenv("MYRM_PROXIES", raising=False)

    assert await resolve_browser_proxy_pool() is None
