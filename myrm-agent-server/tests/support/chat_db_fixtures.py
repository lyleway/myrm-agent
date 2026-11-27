"""In-memory database fixtures for suites that persist chats or search messages.

``db_session`` is a schema-complete SQLite database with ``UnitOfWork`` bound to it; ``fts_db`` adds the
``messages_fts`` trigram table, its sync triggers and the conversation-recall schema; ``session_key_reads_db_session``
points the session-key resolver's own reads (``app.database.connection.get_session``) at ``db_session`` too. Registered
globally through ``pytest_plugins`` in ``tests/conftest.py``. They are plugin modules, not conftests: a conftest that is
also a plugin is registered twice, and pytest then aborts any run that targets its directory.
"""

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from unittest.mock import patch

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models import Base
from app.database.repositories.conversation_recall import CONVERSATION_RECALL_SCHEMA_SQL


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///file:testdb_chat?mode=memory&cache=shared&uri=true")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        from app.database.migrations import ensure_raw_sql_schema

        await ensure_raw_sql_schema(engine)

    testing_session = async_sessionmaker(engine, expire_on_commit=False)

    # Bind UnitOfWork to this in-memory database for repository calls.
    import app.database.repositories.uow as uow_module

    with patch.object(uow_module, "get_session_factory", lambda: testing_session):
        async with testing_session() as session:
            yield session

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
def session_key_reads_db_session(db_session: AsyncSession) -> Iterator[None]:
    """The resolver of a channel message's session key reads through ``get_session``, not through ``UnitOfWork``.

    Without this a test that stores chats through the chat service and resolves keys over them reads two databases.
    """

    @asynccontextmanager
    async def session() -> AsyncIterator[AsyncSession]:
        yield db_session

    with patch("app.database.connection.get_session", session):
        yield


@pytest_asyncio.fixture
async def fts_db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///file:testdb_search?mode=memory&cache=shared&uri=true")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.execute(
            text("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                content=messages,
                content_rowid=rowid,
                tokenize='trigram'
            )
        """)
        )
        await conn.execute(
            text("""
            CREATE TRIGGER IF NOT EXISTS messages_fts_insert AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
            END
        """)
        )
        await conn.execute(
            text("""
            CREATE TRIGGER IF NOT EXISTS messages_fts_delete AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
            END
        """)
        )
        await conn.execute(
            text("""
            CREATE TRIGGER IF NOT EXISTS messages_fts_update AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, content) VALUES('delete', old.rowid, old.content);
                INSERT INTO messages_fts(rowid, content) VALUES (new.rowid, new.content);
            END
        """)
        )
        for sql in CONVERSATION_RECALL_SCHEMA_SQL:
            await conn.execute(text(sql))

    test_session = async_sessionmaker(engine, expire_on_commit=False)

    import app.database.repositories.uow as uow_module

    with patch.object(uow_module, "get_session_factory", lambda: test_session):
        async with test_session() as session:
            yield session

    await engine.dispose()
