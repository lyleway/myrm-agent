"""Deciding an external agent's permission request over REST: the turn that asked carries on, and no agent run is resumed."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from myrm_agent_harness.toolkits.acp.types import PermissionVerdict

from app.database.models.approval import ApprovalRecord
from app.services.approvals.external_agent_permission import ACTION_TYPE, pending_permission_answers
from app.services.approvals.registry import ApprovalRegistry
from app.services.event.app_event_bus import AppEventType


@pytest.fixture
def idle_status() -> Iterator[MagicMock]:
    """The session-status publisher a resumed agent run clears the sidebar indicator through."""
    multiplexer = MagicMock()
    multiplexer_cls = MagicMock()
    multiplexer_cls.get.return_value = multiplexer
    with patch("app.services.agent.streaming_support.multiplexer.WorkspaceMultiplexer", multiplexer_cls):
        yield multiplexer.publish_session_status


@pytest.fixture
def bus() -> Iterator[MagicMock]:
    bus = MagicMock()
    with patch("app.services.approvals.registry.get_event_bus", return_value=bus):
        yield bus


async def _asking(*, severity: str = "warning", command: str = "ls") -> tuple[ApprovalRecord, asyncio.Future[PermissionVerdict]]:
    record = await ApprovalRegistry.create_approval(
        agent_id="agent-1",
        chat_id="chat-door",
        action_type=ACTION_TYPE,
        payload={"command": command, "kind": "execute"},
        reason=f"claude: Run: {command}",
        severity=severity,
    )
    return record, pending_permission_answers.expect(record.id)


def _decided_events(bus: MagicMock) -> list[dict[str, object]]:
    return [event.data for (event,), _ in bus.publish.call_args_list if event.event_type == AppEventType.APPROVAL_RESOLVED]


async def _post(app: FastAPI, path: str, body: dict[str, object]) -> Response:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        return await client.post(f"/api/v1/approvals/{path}", json=body)


@pytest.mark.usefixtures("setup_test_database")
class TestSingleResolve:
    @pytest.mark.parametrize(("decision", "allowed"), [("approve", True), ("deny", False)])
    async def test_the_decision_reaches_the_turn_and_resumes_nothing(
        self, app: FastAPI, idle_status: MagicMock, bus: MagicMock, decision: str, allowed: bool
    ) -> None:
        record, waiting = await _asking()

        response = await _post(app, f"{record.id}/resolve", {"decision": decision})

        assert response.status_code == 200
        assert (await asyncio.wait_for(waiting, timeout=2)).allowed is allowed
        assert [event["action"] for event in _decided_events(bus)] == ["decided"]
        idle_status.assert_not_called()

    async def test_the_reason_for_a_refusal_reaches_the_turn(self, app: FastAPI) -> None:
        record, waiting = await _asking()

        response = await _post(app, f"{record.id}/resolve", {"decision": "reject", "comment": "Not in this repo"})

        assert response.status_code == 200
        assert await asyncio.wait_for(waiting, timeout=2) == PermissionVerdict(allowed=False, message="Not in this repo")


@pytest.mark.usefixtures("setup_test_database")
class TestBatchResolve:
    async def test_the_decision_reaches_the_turn_and_resumes_nothing(
        self, app: FastAPI, idle_status: MagicMock, bus: MagicMock
    ) -> None:
        record, waiting = await _asking()

        response = await _post(app, "batch-resolve", {"approval_ids": [record.id], "decision": "approve"})

        assert response.status_code == 200
        assert (await asyncio.wait_for(waiting, timeout=2)).allowed
        assert [event["action"] for event in _decided_events(bus)] == ["decided"]
        idle_status.assert_not_called()

    async def test_a_command_is_not_waved_through_a_batch_unconfirmed(self, app: FastAPI, bus: MagicMock) -> None:
        record, waiting = await _asking(severity="high", command="rm -rf build")

        response = await _post(app, "batch-resolve", {"approval_ids": [record.id], "decision": "approve"})

        assert response.status_code == 409
        assert not waiting.done()
        assert (await ApprovalRegistry.get_approval(record.id)).status == "PENDING"
        assert _decided_events(bus) == []
        waiting.cancel()
        pending_permission_answers.forget(record.id)

    async def test_a_confirmed_batch_allows_the_command(self, app: FastAPI, idle_status: MagicMock) -> None:
        record, waiting = await _asking(severity="high", command="rm -rf build")

        response = await _post(
            app, "batch-resolve", {"approval_ids": [record.id], "decision": "approve", "confirm_high_risk": True}
        )

        assert response.status_code == 200
        assert (await asyncio.wait_for(waiting, timeout=2)).allowed
        idle_status.assert_not_called()

    async def test_denying_a_batch_needs_no_confirmation(self, app: FastAPI) -> None:
        record, waiting = await _asking(severity="high", command="rm -rf build")

        response = await _post(app, "batch-resolve", {"approval_ids": [record.id], "decision": "deny"})

        assert response.status_code == 200
        assert (await asyncio.wait_for(waiting, timeout=2)) == PermissionVerdict(allowed=False)
