"""The migration removes the retired ``answer_tool`` ID from saved agents and rollback snapshots.

It rewrites only lists that hold the ID and keeps the order of the others, leaves rows that are not a JSON array alone,
does not rewrite the read-only edit history, and leaves an agent or snapshot that validation accepts again.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from app.database import migrations
from app.database.migrations import (
    MIGRATION_STATEMENTS,
    STRIP_RETIRED_ANSWER_TOOL_FROM_AGENTS_SQL,
    STRIP_RETIRED_ANSWER_TOOL_FROM_CRON_JOBS_SQL,
    STRIP_RETIRED_ANSWER_TOOL_FROM_SNAPSHOTS_SQL,
)
from app.database.models import Agent, CronJobModel
from app.database.models.agent import AgentProfileSnapshot
from app.database.models.agent_history import AgentProfileHistory
from app.database.models.base import Base
from app.services.agent.builtin_specs.builtin_tool_ids import (
    InvalidBuiltinToolIdsError,
    normalize_enabled_builtin_tools,
    persist_enabled_builtin_tools,
    strip_legacy_builtin_tool_ids,
)

_HISTORY_DATA = {"enabled_builtin_tools": ["web_search", "answer_tool"]}

# agent id -> ``enabled_builtin_tools`` as stored (None is SQL NULL), including forms no valid save writes.
_AGENT_TOOLS: dict[str, str | None] = {
    "only-retired": '["answer_tool"]',
    "retired-with-others": '["web_search", "answer_tool", "memory", "browser"]',
    "retired-twice": '["answer_tool", "memory", "answer_tool"]',
    "retired-last": '["web_search","answer_tool"]',
    "no-retired": '["web_search", "memory"]',
    "similar-id": '["answer_tool_v2", "web_search"]',
    "empty-list": "[]",
    "json-null": "null",
    "sql-null": None,
    "object-root": '{"answer_tool": 1}',
    "string-root": '"answer_tool"',
    "malformed": "{not json",
}
# The rows the statement rewrites, with the list they hold afterwards.
_AGENT_CHANGED: dict[str, list[str]] = {
    "only-retired": [],
    "retired-with-others": ["web_search", "memory", "browser"],
    "retired-twice": ["memory"],
    "retired-last": ["web_search"],
}

_SNAPSHOT_DATA: dict[str, str] = {
    "snap-with-others": json.dumps({"display_name": "A", "enabled_builtin_tools": ["web_search", "answer_tool", "memory"]}),
    "snap-only-retired": json.dumps({"display_name": "B", "enabled_builtin_tools": ["answer_tool"]}),
    "snap-no-retired": json.dumps({"enabled_builtin_tools": ["web_search"]}),
    "snap-no-key": json.dumps({"display_name": "C"}),
    "snap-null-tools": json.dumps({"enabled_builtin_tools": None}),
    "snap-string-tools": json.dumps({"enabled_builtin_tools": "answer_tool"}),
    "snap-malformed": "{not json",
}
_SNAPSHOT_CHANGED: dict[str, dict[str, object]] = {
    "snap-with-others": {"display_name": "A", "enabled_builtin_tools": ["web_search", "memory"]},
    "snap-only-retired": {"display_name": "B", "enabled_builtin_tools": []},
}

_HOLDER_ID = "snapshot-holder"

# Positions below this one were released before these statements and are recorded in users' ledgers.
_RELEASED_BEFORE = 262
_FIRST = MIGRATION_STATEMENTS.index(STRIP_RETIRED_ANSWER_TOOL_FROM_AGENTS_SQL)


def _agent(agent_id: str) -> Agent:
    return Agent(
        id=agent_id,
        name=agent_id,
        description="",
        skill_ids=[],
        mcp_servers=[],
        subagent_ids=[],
        model_config={},
    )


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'agents.db'}")
    tables = [Base.metadata.tables[name] for name in ("agents", "agent_profile_snapshots", "agent_profile_history")]
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync: Base.metadata.create_all(sync, tables=tables))
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add_all(_agent(agent_id) for agent_id in (*_AGENT_TOOLS, _HOLDER_ID))
        await db.flush()
        db.add_all(AgentProfileSnapshot(id=snapshot_id, agent_id=_HOLDER_ID, snapshot_data={}) for snapshot_id in _SNAPSHOT_DATA)
        db.add(AgentProfileHistory(id="history-1", agent_id=_HOLDER_ID, version=1, name="v1", snapshot_data=_HISTORY_DATA))
        await db.flush()
        # Raw writes store the exact text, including values the ORM would never produce.
        for agent_id, raw in _AGENT_TOOLS.items():
            await db.execute(text("UPDATE agents SET enabled_builtin_tools = :raw WHERE id = :id"), {"raw": raw, "id": agent_id})
        for snapshot_id, raw in _SNAPSHOT_DATA.items():
            await db.execute(
                text("UPDATE agent_profile_snapshots SET snapshot_data = :raw WHERE id = :id"),
                {"raw": raw, "id": snapshot_id},
            )
        await db.commit()
    yield factory
    await engine.dispose()


async def _run(factory: async_sessionmaker[AsyncSession], statement: str) -> int:
    async with factory() as db:
        result = await db.execute(text(statement))
        await db.commit()
        return result.rowcount


async def _stored(factory: async_sessionmaker[AsyncSession], sql: str) -> dict[str, str | None]:
    async with factory() as db:
        return {row[0]: row[1] for row in (await db.execute(text(sql))).all()}


async def _agent_tools(factory: async_sessionmaker[AsyncSession]) -> dict[str, str | None]:
    return await _stored(factory, "SELECT id, enabled_builtin_tools FROM agents")


async def _snapshots(factory: async_sessionmaker[AsyncSession]) -> dict[str, str | None]:
    return await _stored(factory, "SELECT id, snapshot_data FROM agent_profile_snapshots")


def _loads(raw: str | None) -> object:
    assert raw is not None
    return json.loads(raw)


def test_the_statements_are_registered_once_after_every_released_one_and_in_order() -> None:
    statements = [
        STRIP_RETIRED_ANSWER_TOOL_FROM_AGENTS_SQL,
        STRIP_RETIRED_ANSWER_TOOL_FROM_SNAPSHOTS_SQL,
        STRIP_RETIRED_ANSWER_TOOL_FROM_CRON_JOBS_SQL,
    ]
    for statement in statements:
        assert MIGRATION_STATEMENTS.count(statement) == 1
    assert _FIRST >= _RELEASED_BEFORE
    assert MIGRATION_STATEMENTS[_FIRST : _FIRST + 3] == statements


@pytest.mark.asyncio
async def test_agents_lose_only_the_retired_id(session_factory: async_sessionmaker[AsyncSession]) -> None:
    changed = await _run(session_factory, STRIP_RETIRED_ANSWER_TOOL_FROM_AGENTS_SQL)

    after = await _agent_tools(session_factory)
    assert changed == len(_AGENT_CHANGED)
    for agent_id, raw in _AGENT_TOOLS.items():
        if agent_id in _AGENT_CHANGED:
            assert _loads(after[agent_id]) == _AGENT_CHANGED[agent_id], agent_id
        else:
            assert after[agent_id] == raw, agent_id


@pytest.mark.asyncio
async def test_snapshots_lose_only_the_retired_id(session_factory: async_sessionmaker[AsyncSession]) -> None:
    changed = await _run(session_factory, STRIP_RETIRED_ANSWER_TOOL_FROM_SNAPSHOTS_SQL)

    after = await _snapshots(session_factory)
    assert changed == len(_SNAPSHOT_CHANGED)
    for snapshot_id, raw in _SNAPSHOT_DATA.items():
        if snapshot_id in _SNAPSHOT_CHANGED:
            assert _loads(after[snapshot_id]) == _SNAPSHOT_CHANGED[snapshot_id], snapshot_id
        else:
            assert after[snapshot_id] == raw, snapshot_id


@pytest.mark.asyncio
async def test_the_edit_history_keeps_what_each_version_held(session_factory: async_sessionmaker[AsyncSession]) -> None:
    await _run(session_factory, STRIP_RETIRED_ANSWER_TOOL_FROM_AGENTS_SQL)
    await _run(session_factory, STRIP_RETIRED_ANSWER_TOOL_FROM_SNAPSHOTS_SQL)

    history = await _stored(session_factory, "SELECT id, snapshot_data FROM agent_profile_history")
    assert _loads(history["history-1"]) == _HISTORY_DATA


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "statement",
    [STRIP_RETIRED_ANSWER_TOOL_FROM_AGENTS_SQL, STRIP_RETIRED_ANSWER_TOOL_FROM_SNAPSHOTS_SQL],
)
async def test_running_a_statement_again_changes_nothing(
    session_factory: async_sessionmaker[AsyncSession], statement: str
) -> None:
    await _run(session_factory, statement)
    agents, snapshots = await _agent_tools(session_factory), await _snapshots(session_factory)

    assert await _run(session_factory, statement) == 0
    assert (await _agent_tools(session_factory), await _snapshots(session_factory)) == (agents, snapshots)


@pytest.mark.asyncio
async def test_a_cleaned_agent_lists_what_the_read_path_already_resolved(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """The first start after the upgrade changes nothing an agent runs with: the read path dropped the ID anyway."""
    await _run(session_factory, STRIP_RETIRED_ANSWER_TOOL_FROM_AGENTS_SQL)

    after = await _agent_tools(session_factory)
    for agent_id in _AGENT_CHANGED:
        before = json.loads(_AGENT_TOOLS[agent_id] or "null")
        cleaned = _loads(after[agent_id])
        assert isinstance(cleaned, list)
        assert normalize_enabled_builtin_tools(strip_legacy_builtin_tool_ids(before)) == normalize_enabled_builtin_tools(cleaned)


@pytest.mark.asyncio
async def test_validation_accepts_a_cleaned_list_where_it_rejected_the_stored_one(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Saving an agent and rolling back to a snapshot both validate the list with the same strict check."""
    await _run(session_factory, STRIP_RETIRED_ANSWER_TOOL_FROM_AGENTS_SQL)
    await _run(session_factory, STRIP_RETIRED_ANSWER_TOOL_FROM_SNAPSHOTS_SQL)
    agents, snapshots = await _agent_tools(session_factory), await _snapshots(session_factory)

    for agent_id in _AGENT_CHANGED:
        with pytest.raises(InvalidBuiltinToolIdsError):
            persist_enabled_builtin_tools(json.loads(_AGENT_TOOLS[agent_id] or "null"))
        cleaned = _loads(agents[agent_id])
        assert persist_enabled_builtin_tools(cleaned) == cleaned
    for snapshot_id in _SNAPSHOT_CHANGED:
        stored = json.loads(_SNAPSHOT_DATA[snapshot_id])["enabled_builtin_tools"]
        with pytest.raises(InvalidBuiltinToolIdsError):
            persist_enabled_builtin_tools(stored)
        cleaned_snapshot = _loads(snapshots[snapshot_id])
        assert isinstance(cleaned_snapshot, dict)
        assert (
            persist_enabled_builtin_tools(cleaned_snapshot["enabled_builtin_tools"]) == cleaned_snapshot["enabled_builtin_tools"]
        )


@pytest.mark.asyncio
async def test_the_migration_ledger_applies_the_statements_when_a_database_upgrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    engine: AsyncEngine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'upgrade.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    monkeypatch.setattr(migrations, "MIGRATION_STATEMENTS", MIGRATION_STATEMENTS[:_FIRST])
    await migrations.run_migrations(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        db.add(_agent("saved"))
        db.add(CronJobModel(id="job", name="job", job_type="agent", schedule={"kind": "interval"}))
        await db.flush()
        db.add(AgentProfileSnapshot(id="snap", agent_id="saved", snapshot_data={}))
        await db.flush()
        await db.execute(text("UPDATE agents SET enabled_builtin_tools = :raw"), {"raw": _AGENT_TOOLS["retired-with-others"]})
        await db.execute(
            text("UPDATE agent_profile_snapshots SET snapshot_data = :raw"), {"raw": _SNAPSHOT_DATA["snap-with-others"]}
        )
        await db.execute(text("UPDATE cron_jobs SET tools_allowed = :raw"), {"raw": _AGENT_TOOLS["retired-with-others"]})
        await db.commit()

    monkeypatch.setattr(migrations, "MIGRATION_STATEMENTS", MIGRATION_STATEMENTS[: _FIRST + 3])
    assert await migrations.has_pending_migrations(engine)
    await migrations.run_migrations(engine)

    assert not await migrations.has_pending_migrations(engine)
    assert _loads((await _agent_tools(factory))["saved"]) == _AGENT_CHANGED["retired-with-others"]
    assert _loads((await _snapshots(factory))["snap"]) == _SNAPSHOT_CHANGED["snap-with-others"]
    cron = await _stored(factory, "SELECT id, tools_allowed FROM cron_jobs")
    assert _loads(cron["job"]) == _AGENT_CHANGED["retired-with-others"]
    await engine.dispose()
