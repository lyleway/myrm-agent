"""External agent permission requests left by a previous server process are closed when the next one starts."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Coroutine, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models.base import Base
from app.lifecycle.schedulers import security
from app.services.approvals.external_agent_permission import ACTION_TYPE
from app.services.approvals.registry import ApprovalRegistry


@pytest_asyncio.fixture
async def wired(tmp_path: Path) -> AsyncIterator[None]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'approvals.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    @asynccontextmanager
    async def get_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    with (
        patch("app.services.approvals.registry.get_session", get_session),
        patch("app.services.approvals.chat_peer_card.get_session", get_session),
        patch("app.services.approvals.registry.get_event_bus", return_value=MagicMock()),
        patch.object(ApprovalRegistry, "_sync_copilot_pending", AsyncMock()),
    ):
        yield
    await engine.dispose()


async def _pending(action_type: str) -> str:
    record = await ApprovalRegistry.create_approval(
        agent_id="agent-1", action_type=action_type, payload={}, reason="r", chat_id="chat-1"
    )
    return record.id


@pytest.mark.usefixtures("wired")
class TestWithdrawOrphanedPermissionRequests:
    async def test_requests_nothing_waits_for_any_more_are_closed_and_other_approvals_stay(self) -> None:
        asked, also_asked, other = await _pending(ACTION_TYPE), await _pending(ACTION_TYPE), await _pending("shell_execute")

        await security._withdraw_orphaned_permission_requests()

        statuses = {i: (await ApprovalRegistry.get_approval(i)).status for i in (asked, also_asked, other)}
        assert statuses == {asked: "CANCELLED", also_asked: "CANCELLED", other: "PENDING"}
        assert (await ApprovalRegistry.get_approval(asked)).reason.endswith("[Withdrawn: the server restarted]")

    async def test_a_failure_does_not_stop_the_scheduler_from_starting(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            patch.object(ApprovalRegistry, "withdraw_pending_of_type", AsyncMock(side_effect=RuntimeError("db is locked"))),
            caplog.at_level(logging.WARNING),
        ):
            await security._withdraw_orphaned_permission_requests()

        assert "Withdrawing orphaned external agent permission requests failed: db is locked" in caplog.text


class _FakeScheduler:
    """Stands in for APScheduler's ``AsyncScheduler``: records what is scheduled and when."""

    calls: list[str] = []

    async def __aenter__(self) -> _FakeScheduler:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def add_schedule(self, *args: object, **kwargs: object) -> None:
        self.calls.append("scheduled")

    async def start_in_background(self) -> None:
        self.calls.append("started")


@pytest.fixture
def calls() -> Iterator[list[str]]:
    _FakeScheduler.calls = []
    yield _FakeScheduler.calls


async def test_the_orphans_are_closed_before_the_ttl_sweep_is_scheduled(calls: list[str]) -> None:
    started: list[Coroutine[object, object, None]] = []

    async def withdraw() -> None:
        calls.append("withdrawn")

    def spawn(coroutine: Coroutine[object, object, None], *, name: str) -> asyncio.Task[None]:
        started.append(coroutine)
        return asyncio.create_task(asyncio.sleep(0))

    with (
        patch("apscheduler.AsyncScheduler", _FakeScheduler),
        patch.object(security, "spawn_observed_task", spawn),
        patch.object(security, "_withdraw_orphaned_permission_requests", withdraw),
    ):
        await security.start_approval_ttl_scheduler()
        (run_scheduler,) = started
        task = asyncio.create_task(run_scheduler)
        while "started" not in calls:
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert calls == ["withdrawn", "scheduled", "started"]
