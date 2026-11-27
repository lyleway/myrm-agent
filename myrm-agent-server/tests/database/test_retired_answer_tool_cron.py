"""The migration keeps a scheduled job as restricted as it was when it drops the retired ``answer_tool`` ID.

A job's ``tools_allowed`` list limits the tools it may use, and the row mapper reads a stored empty list as "no limit".
Dropping the ID from a list that holds nothing else would give the job every tool of its agent, so such a list stays
as it is and still allows nothing.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Mapping
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.core.cron.adapters.sqlalchemy_mapping import job_to_domain
from app.core.cron.adapters.tools_policy import intersect_cron_enabled_builtin_tools, resolve_cron_runtime_tool_flags
from app.database.migrations import STRIP_RETIRED_ANSWER_TOOL_FROM_CRON_JOBS_SQL
from app.database.models import CronJobModel
from app.database.models.base import Base

# job id -> ``tools_allowed`` as stored (None is SQL NULL), including forms no valid save writes.
_JOB_TOOLS: dict[str, str | None] = {
    "retired-with-others": '["web_search", "answer_tool", "memory"]',
    "retired-with-baseline": '["file_ops", "answer_tool"]',
    "retired-before-baselines": '["answer_tool", "file_ops", "code_execute"]',
    "only-retired": '["answer_tool"]',
    "only-retired-twice": '["answer_tool", "answer_tool"]',
    "no-retired": '["web_search"]',
    "similar-id": '["answer_tool_v2", "web_search"]',
    "empty-list": "[]",
    "json-null": "null",
    "sql-null": None,
    "object-root": '{"answer_tool": 1}',
    "malformed": "{not json",
}
# The rows the statement rewrites, with the list they hold afterwards.
_JOB_CHANGED: dict[str, list[str]] = {
    "retired-with-others": ["web_search", "memory"],
    "retired-with-baseline": ["file_ops"],
    "retired-before-baselines": ["file_ops", "code_execute"],
}
# The forms a save writes; the other rows are only checked as text, because the row mapper has no meaning for them.
_SAVED_FORMS = [job_id for job_id in _JOB_TOOLS if job_id not in {"object-root", "malformed"}]

# What a job's agent may offer: after the upgrade no agent holds the retired ID, so none of these does.
_AGENT_TOOL_SETS: list[list[str]] = [
    ["web_search", "memory"],
    [],
    ["web_search", "memory", "structured_clarify", "browser"],
    ["file_ops", "code_execute"],
]


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'cron.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=[Base.metadata.tables["cron_jobs"]]))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all(
            CronJobModel(id=job_id, name=job_id, job_type="agent", schedule={"kind": "interval", "interval_ms": 60000})
            for job_id in _JOB_TOOLS
        )
        await db.flush()
        # Raw writes store the exact text, including values the ORM would never produce.
        for job_id, raw in _JOB_TOOLS.items():
            await db.execute(text("UPDATE cron_jobs SET tools_allowed = :raw WHERE id = :id"), {"raw": raw, "id": job_id})
        await db.commit()
    yield factory
    await engine.dispose()


async def _run(factory: async_sessionmaker[AsyncSession]) -> int:
    async with factory() as db:
        result = await db.execute(text(STRIP_RETIRED_ANSWER_TOOL_FROM_CRON_JOBS_SQL))
        await db.commit()
        return result.rowcount


async def _stored(factory: async_sessionmaker[AsyncSession]) -> dict[str, str | None]:
    async with factory() as db:
        return {row[0]: row[1] for row in (await db.execute(text("SELECT id, tools_allowed FROM cron_jobs"))).all()}


async def _tools_allowed(factory: async_sessionmaker[AsyncSession]) -> dict[str, tuple[str, ...] | None]:
    """The restriction each job runs with: the stored list read by the real row mapper."""
    async with factory() as db:
        models = (await db.execute(select(CronJobModel).where(CronJobModel.id.in_(_SAVED_FORMS)))).scalars().all()
        return {model.id: job_to_domain(model).tools_allowed for model in models}


def _runtime(tools_allowed: tuple[str, ...] | None, agent_tools: list[str]) -> Mapping[str, object]:
    effective = intersect_cron_enabled_builtin_tools(agent_tools, tools_allowed)
    return {"effective": effective, **resolve_cron_runtime_tool_flags(effective, tools_allowed)}


@pytest.mark.asyncio
async def test_jobs_lose_the_retired_id_only_when_other_ids_stay(session_factory: async_sessionmaker[AsyncSession]) -> None:
    changed = await _run(session_factory)

    after = await _stored(session_factory)
    assert changed == len(_JOB_CHANGED)
    for job_id, raw in _JOB_TOOLS.items():
        if job_id in _JOB_CHANGED:
            assert json.loads(after[job_id] or "null") == _JOB_CHANGED[job_id], job_id
        else:
            assert after[job_id] == raw, job_id


@pytest.mark.asyncio
async def test_running_the_statement_again_changes_nothing(session_factory: async_sessionmaker[AsyncSession]) -> None:
    await _run(session_factory)
    stored = await _stored(session_factory)

    assert await _run(session_factory) == 0
    assert await _stored(session_factory) == stored


@pytest.mark.asyncio
async def test_no_job_runs_with_a_tool_it_could_not_use_before(session_factory: async_sessionmaker[AsyncSession]) -> None:
    before = await _tools_allowed(session_factory)
    await _run(session_factory)
    after = await _tools_allowed(session_factory)

    for job_id in _SAVED_FORMS:
        for agent_tools in _AGENT_TOOL_SETS:
            assert _runtime(after[job_id], agent_tools) == _runtime(before[job_id], agent_tools), (job_id, agent_tools)


@pytest.mark.asyncio
async def test_a_job_that_allowed_only_the_retired_id_still_allows_nothing(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await _run(session_factory)

    after = await _tools_allowed(session_factory)
    for job_id in ("only-retired", "only-retired-twice"):
        assert after[job_id] is not None, job_id
        for agent_tools in _AGENT_TOOL_SETS:
            assert _runtime(after[job_id], agent_tools)["effective"] == [], (job_id, agent_tools)


@pytest.mark.asyncio
async def test_the_row_mapper_reads_a_stored_empty_list_as_no_limit(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """The reason a list holding only the retired ID is not rewritten: ``[]`` would free the job from every limit."""
    assert (await _tools_allowed(session_factory))["empty-list"] is None
    assert _runtime(None, ["web_search", "memory"])["effective"] == ["web_search", "memory"]
