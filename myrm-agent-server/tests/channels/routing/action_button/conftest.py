"""Fixtures of the action-button approval tests: an in-memory approvals database and an event bus that records."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest

from app.database.models.base import Base
from tests.channels.routing.action_button.testkit import engine, session


@pytest.fixture(autouse=True, scope="module")
def _create_tables() -> Iterator[None]:
    loop = asyncio.new_event_loop()
    loop.run_until_complete(_create())
    yield
    loop.run_until_complete(_drop())
    loop.close()


async def _create() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _drop() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture(autouse=True)
def _patch_db() -> Iterator[None]:
    with patch("app.services.approvals.registry.get_session", session):
        yield


@pytest.fixture(autouse=True)
def event_bus() -> Iterator[MagicMock]:
    """The bus the approval registry and the router's fallback publish to."""
    bus = MagicMock()
    with (
        patch("app.services.approvals.registry.get_event_bus", return_value=bus),
        patch("app.services.event.app_event_bus.get_event_bus", return_value=bus),
    ):
        yield bus
