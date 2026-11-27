"""Shared app-under-test of the artifact-apps API tests: an in-memory registry with an HTML and a Markdown artifact."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.api.artifact_apps import router as artifact_apps_router
from app.api.dependencies import get_workspace_root
from app.database.connection import get_db
from app.database.models import Artifact, ArtifactVersion, Base
from app.services.artifact_apps.rate_limit import AI_LIMITER, FETCH_LIMITER

_VERSION_SHA = "a" * 64


@pytest.fixture
async def session_factory() -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as db:
        db.add_all(
            [
                Artifact(id="clock", name="clock.html", chat_id="chat-1"),
                Artifact(id="notes", name="notes.md"),
            ]
        )
        db.add_all(
            [
                ArtifactVersion(id="v1", artifact_id="clock", vault_uri="vault://o1", sha256_hash=_VERSION_SHA),
                ArtifactVersion(id="v0", artifact_id="notes", vault_uri="vault://o2", sha256_hash=_VERSION_SHA),
            ]
        )
        await db.commit()
    yield factory
    await engine.dispose()


@pytest.fixture
async def client(session_factory: async_sessionmaker[AsyncSession], tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    app = FastAPI()
    app.include_router(artifact_apps_router, prefix="/api/v1")

    async def database() -> AsyncIterator[AsyncSession]:
        async with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = database
    app.dependency_overrides[get_workspace_root] = lambda: tmp_path
    for limiter in (FETCH_LIMITER, AI_LIMITER):
        limiter._starts.clear()
        limiter._in_flight.clear()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://apps.test") as http:
        yield http
