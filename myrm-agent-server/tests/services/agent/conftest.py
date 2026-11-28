"""Fixtures shared by the built-in agent tests of this directory."""

from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.database.models.base import Base

# ``stream_loop`` and ``stream_finalize`` bind the agent stream and the timer arming by name when they are first
# imported, and tests under this directory replace both with stand-ins. A module imported for the first time inside
# such a test keeps the stand-in for every test after it, so both are imported here, before any test runs.
from app.services.agent.stream_session import stream_finalize as _stream_finalize  # noqa: F401
from app.services.agent.stream_session import stream_loop as _stream_loop  # noqa: F401


@pytest.fixture
async def test_db():
    """In-memory SQLite database that the built-in agent initializer and AgentService read and write."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    TestSession = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    @asynccontextmanager
    async def mock_get_session():
        async with TestSession() as session:
            try:
                yield session
            finally:
                await session.close()

    def mock_get_session_factory():
        return TestSession

    with (
        patch("app.database.connection.get_session", mock_get_session),
        patch("app.services.agent.builtin_specs.builtin_initializer.get_session", mock_get_session),
        patch(
            "app.database.repositories.uow.get_session_factory",
            mock_get_session_factory,
        ),
        patch("app.database.connection.get_session_factory", mock_get_session_factory),
    ):
        yield TestSession

    await engine.dispose()
