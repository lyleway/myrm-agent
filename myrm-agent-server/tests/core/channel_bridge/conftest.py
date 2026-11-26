"""Fixtures of the channel-bridge tests."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import delete

from app.database.models import UserConfig
from app.database.repositories.uow import UnitOfWork


@pytest.fixture
async def topic_channel() -> AsyncIterator[str]:
    """A channel of its own: the stored topic config and its write generation are per channel."""
    name = f"topics{uuid.uuid4().hex[:8]}"
    yield name
    async with UnitOfWork(write=True) as uow:
        assert uow.session is not None
        await uow.session.execute(delete(UserConfig).where(UserConfig.config_key.like(f"%{name}%")))
