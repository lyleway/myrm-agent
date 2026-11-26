"""An external agent's permission request: how a decision on it, made through any door, reaches the turn that asked."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from myrm_agent_harness.toolkits.acp.types import PermissionVerdict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models.approval import ApprovalRecord
from app.database.models.base import Base
from app.services.approvals.external_agent_permission import (
    ACTION_TYPE,
    DECISION_NOTE_CHARS,
    DECISION_NOTE_KEY,
    NO_ANSWER_MESSAGE,
    PendingPermissionAnswers,
    pending_permission_answers,
    verdict_of,
)
from app.services.approvals.registry import ApprovalRegistry
from app.services.event.app_event_bus import AppEventType


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'approvals.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def bus(session_factory: async_sessionmaker[AsyncSession]) -> Iterator[MagicMock]:
    @asynccontextmanager
    async def get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    bus = MagicMock()
    with (
        patch("app.services.approvals.registry.get_session", get_session),
        patch("app.services.approvals.chat_peer_card.get_session", get_session),
        patch("app.services.approvals.registry.get_event_bus", return_value=bus),
        patch.object(ApprovalRegistry, "_sync_copilot_pending", AsyncMock()),
    ):
        yield bus


@pytest.fixture(autouse=True)
def no_waiters() -> Iterator[None]:
    yield
    assert pending_permission_answers.waiting_count == 0, "a test left a request waiting"


async def _request(
    action_type: str = ACTION_TYPE, *, chat_id: str | None = "chat-1", expires_in: float | None = None
) -> ApprovalRecord:
    return await ApprovalRegistry.create_approval(
        agent_id="agent-1",
        action_type=action_type,
        payload={"command": "ls"},
        reason="claude: Run: ls",
        chat_id=chat_id,
        expires_at=None if expires_in is None else datetime.now(timezone.utc) + timedelta(seconds=expires_in),
    )


def _resolved_events(bus: MagicMock) -> list[dict[str, object]]:
    return [event.data for (event,), _ in bus.publish.call_args_list if event.event_type == AppEventType.APPROVAL_RESOLVED]


class TestVerdictOf:
    def test_only_an_approval_allows(self) -> None:
        assert verdict_of("APPROVED").allowed

    @pytest.mark.parametrize("status", ["REJECTED", "CANCELLED", "PENDING"])
    def test_anything_else_refuses_without_a_reason_of_its_own(self, status: str) -> None:
        verdict = verdict_of(status)
        assert (verdict.allowed, verdict.message) == (False, None)

    def test_an_unanswered_request_says_so(self) -> None:
        verdict = verdict_of("TIMEOUT")
        assert (verdict.allowed, verdict.message) == (False, NO_ANSWER_MESSAGE)

    def test_a_refusal_carries_the_reason_the_person_gave(self) -> None:
        verdict = verdict_of("REJECTED", "Use /tmp instead")
        assert (verdict.allowed, verdict.message) == (False, "Use /tmp instead")

    def test_a_reason_does_not_change_what_an_approval_or_a_timeout_says(self) -> None:
        assert verdict_of("APPROVED", "fine") == PermissionVerdict(allowed=True)
        assert verdict_of("TIMEOUT", "fine") == PermissionVerdict(allowed=False, message=NO_ANSWER_MESSAGE)


class TestPendingPermissionAnswers:
    async def test_a_decision_resolves_the_future_of_the_request(self) -> None:
        answers = PendingPermissionAnswers()
        waiting = answers.expect("a-1")

        assert answers.settle(ApprovalRecord(id="a-1", status="APPROVED"))

        assert (await waiting).allowed

    async def test_a_decision_nobody_waits_for_is_reported_as_such(self) -> None:
        assert not PendingPermissionAnswers().settle(ApprovalRecord(id="a-1", status="APPROVED"))

    async def test_a_request_is_settled_once(self) -> None:
        answers = PendingPermissionAnswers()
        answers.expect("a-1")

        assert answers.settle(ApprovalRecord(id="a-1", status="REJECTED"))
        assert not answers.settle(ApprovalRecord(id="a-1", status="APPROVED"))

    async def test_a_forgotten_request_is_not_settled(self) -> None:
        answers = PendingPermissionAnswers()
        waiting = answers.expect("a-1")

        answers.forget("a-1")

        assert not answers.settle(ApprovalRecord(id="a-1", status="APPROVED"))
        waiting.cancel()

    async def test_a_decision_made_on_another_thread_still_reaches_the_turn(self) -> None:
        answers = PendingPermissionAnswers()
        waiting = answers.expect("a-1")

        await asyncio.to_thread(answers.settle, ApprovalRecord(id="a-1", status="APPROVED"))

        assert (await asyncio.wait_for(waiting, timeout=2)).allowed


class TestDecisionsReachTheTurn:
    @pytest.mark.parametrize(("decision", "allowed"), [("approve", True), ("deny", False)])
    async def test_a_decision_through_the_registry_wakes_the_turn(self, decision: str, allowed: bool) -> None:
        record = await _request()
        waiting = pending_permission_answers.expect(record.id)

        await ApprovalRegistry.resolve_approval(record.id, decision)

        assert (await asyncio.wait_for(waiting, timeout=2)).allowed is allowed

    async def test_the_reason_for_a_refusal_reaches_the_turn_and_stays_on_the_approval(self) -> None:
        record = await _request()
        waiting = pending_permission_answers.expect(record.id)

        await ApprovalRegistry.resolve_approval(record.id, "deny", comment="  Use /tmp instead  ")

        assert await asyncio.wait_for(waiting, timeout=2) == PermissionVerdict(allowed=False, message="Use /tmp instead")
        assert (await ApprovalRegistry.get_approval(record.id)).payload[DECISION_NOTE_KEY] == "Use /tmp instead"

    @pytest.mark.parametrize("comment", [None, "", "   \n"])
    async def test_a_refusal_without_a_reason_carries_none(self, comment: str | None) -> None:
        record = await _request()
        waiting = pending_permission_answers.expect(record.id)

        await ApprovalRegistry.resolve_approval(record.id, "deny", comment=comment)

        assert await asyncio.wait_for(waiting, timeout=2) == PermissionVerdict(allowed=False)
        assert DECISION_NOTE_KEY not in (await ApprovalRegistry.get_approval(record.id)).payload

    async def test_a_long_reason_is_cut(self) -> None:
        record = await _request()
        waiting = pending_permission_answers.expect(record.id)

        await ApprovalRegistry.resolve_approval(record.id, "deny", comment="x" * 5_000)

        assert len((await asyncio.wait_for(waiting, timeout=2)).message or "") == DECISION_NOTE_CHARS

    async def test_a_comment_on_an_approval_allows_it_as_it_is(self) -> None:
        record = await _request()
        waiting = pending_permission_answers.expect(record.id)

        await ApprovalRegistry.resolve_approval(record.id, "approve", comment="go ahead")

        assert await asyncio.wait_for(waiting, timeout=2) == PermissionVerdict(allowed=True)

    async def test_other_approvals_keep_no_comment(self) -> None:
        other = await _request("shell_execute")

        await ApprovalRegistry.resolve_approval(other.id, "deny", comment="no thanks")

        assert DECISION_NOTE_KEY not in (await ApprovalRegistry.get_approval(other.id)).payload

    async def test_a_decision_closes_the_cards_of_the_request_on_every_device(self, bus: MagicMock) -> None:
        record = await _request()
        pending_permission_answers.expect(record.id)

        await ApprovalRegistry.resolve_approval(record.id, "approve")

        assert _resolved_events(bus) == [
            {"action": "decided", "approval_id": record.id, "chat_id": "chat-1", "agent_id": "agent-1", "decision": "APPROVED"}
        ]

    async def test_other_approvals_are_resolved_without_touching_waiting_turns(self, bus: MagicMock) -> None:
        waiting_record = await _request()
        waiting = pending_permission_answers.expect(waiting_record.id)
        other = await _request("shell_execute")

        await ApprovalRegistry.resolve_approval(other.id, "approve")

        assert not waiting.done()
        assert _resolved_events(bus) == []
        waiting.cancel()
        pending_permission_answers.forget(waiting_record.id)

    async def test_an_expired_request_refuses_the_turn_and_closes_its_cards(self, bus: MagicMock) -> None:
        record = await _request(expires_in=-1)
        waiting = pending_permission_answers.expect(record.id)

        assert await ApprovalRegistry.cleanup_expired_approvals() == 1

        verdict = await asyncio.wait_for(waiting, timeout=2)
        assert (verdict.allowed, verdict.message) == (False, NO_ANSWER_MESSAGE)
        assert [event["action"] for event in _resolved_events(bus)] == ["expired"]
        assert (await ApprovalRegistry.get_approval(record.id)).status == "TIMEOUT"


class TestWithdraw:
    async def test_a_pending_request_is_closed_and_its_cards_with_it(self, bus: MagicMock) -> None:
        record = await _request()

        assert await ApprovalRegistry.withdraw(record.id, status="CANCELLED", note="Withdrawn: the turn ended")

        closed = await ApprovalRegistry.get_approval(record.id)
        assert closed.status == "CANCELLED"
        assert closed.resolved_at is not None
        assert closed.reason.endswith("[Withdrawn: the turn ended]")
        assert [(event["action"], event["decision"]) for event in _resolved_events(bus)] == [("withdrawn", "CANCELLED")]

    async def test_a_decided_request_stays_as_decided(self, bus: MagicMock) -> None:
        record = await _request()
        pending_permission_answers.expect(record.id)
        await ApprovalRegistry.resolve_approval(record.id, "approve")
        bus.publish.reset_mock()

        assert not await ApprovalRegistry.withdraw(record.id, status="CANCELLED", note="late")

        assert (await ApprovalRegistry.get_approval(record.id)).status == "APPROVED"
        assert _resolved_events(bus) == []

    async def test_an_unknown_request_is_not_withdrawn(self) -> None:
        assert not await ApprovalRegistry.withdraw("nope", status="CANCELLED", note="n/a")

    async def test_every_pending_request_of_a_type_is_withdrawn_and_nothing_else(self, bus: MagicMock) -> None:
        first, second = await _request(), await _request(chat_id="chat-2")
        decided = await _request()
        await ApprovalRegistry.resolve_approval(decided.id, "deny")
        other = await _request("shell_execute")
        bus.publish.reset_mock()

        count = await ApprovalRegistry.withdraw_pending_of_type(ACTION_TYPE, status="CANCELLED", note="Withdrawn: restart")

        assert count == 2
        statuses = {r.id: (await ApprovalRegistry.get_approval(r.id)).status for r in (first, second, decided, other)}
        assert statuses == {first.id: "CANCELLED", second.id: "CANCELLED", decided.id: "REJECTED", other.id: "PENDING"}
        assert sorted(event["approval_id"] for event in _resolved_events(bus)) == sorted([first.id, second.id])

    async def test_withdrawing_with_nothing_pending_is_a_no_op(self) -> None:
        assert await ApprovalRegistry.withdraw_pending_of_type(ACTION_TYPE, status="CANCELLED", note="n/a") == 0
