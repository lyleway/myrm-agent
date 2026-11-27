"""The chat's permission broker: what a person is shown of an external agent's request, and how their answer returns."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from myrm_agent_harness.toolkits.acp.types import PermissionRequest, PermissionVerdict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database.models.approval import ApprovalRecord
from app.database.models.base import Base
from app.services.approvals.external_agent_permission import ACTION_TYPE, NO_ANSWER_MESSAGE, pending_permission_answers
from app.services.approvals.registry import ApprovalRegistry
from app.services.event.app_event_bus import AppEventType
from app.services.external_agents.permission_broker import ChatPermissionBroker, permission_payload
from tests.support.fake_secrets import github_token

_TOKEN = github_token("a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8")
_WORKSPACE = "/work/repo"


def _request(
    *,
    kind: str = "execute",
    title: str = "Run: ls -la",
    input: dict[str, object] | None = None,
    paths: tuple[str, ...] = (),
) -> PermissionRequest:
    return PermissionRequest(
        agent="claude",
        session_id="claude-chat-1",
        tool_name="Bash",
        kind=kind,
        title=title,
        input={"command": "ls -la"} if input is None else input,
        paths=paths,
        tool_call_id="call-1",
    )


class TestPermissionPayload:
    def test_it_says_what_the_agent_wants_to_do_and_where(self) -> None:
        payload = permission_payload(_request(paths=("/work/repo/a.txt",)), _WORKSPACE)

        assert payload == {
            "action_type": ACTION_TYPE,
            "agent": "claude",
            "tool_name": "Bash",
            "kind": "execute",
            "title": "Run: ls -la",
            "tool_input": {"command": "ls -la"},
            "paths": ["/work/repo/a.txt"],
            "workspace": _WORKSPACE,
            "session_id": "claude-chat-1",
            "command": "ls -la",
        }

    def test_secrets_are_masked_wherever_the_agent_put_them(self) -> None:
        command = f"curl -H 'Authorization: Bearer {_TOKEN}' https://example.com"
        request = _request(
            title=f"Run: {command}",
            input={"command": command, "env": {"OPENAI_API_KEY": "sk-abcdefghijklmnopqrstuvwxyz012345"}},
        )

        payload = permission_payload(request, _WORKSPACE)

        assert _TOKEN not in str(payload)
        assert "sk-abcdefghijklmnopqrstuvwxyz012345" not in str(payload)
        assert payload["command"] == "curl -H 'Authorization: Bearer ***' https://example.com"

    def test_a_secret_cut_by_the_display_limit_is_still_masked(self) -> None:
        # The shown part would end in the middle of the token, so the mask has to be applied before the cut.
        command = "x" * 1_980 + " " + _TOKEN

        payload = permission_payload(_request(input={"command": command}), _WORKSPACE)

        assert _TOKEN[:12] not in str(payload)

    def test_invisible_characters_are_made_visible(self) -> None:
        payload = permission_payload(_request(input={"command": "rm\u200b -rf /tmp/x"}), _WORKSPACE)

        assert payload["command"] == "rm\\u{200B} -rf /tmp/x"

    def test_what_is_shown_is_bounded(self) -> None:
        payload = permission_payload(
            _request(
                title="t" * 5_000,
                input={"command": "c" * 50_000, "content": "d" * 50_000},
                paths=tuple("/p" * 400 for _ in range(30)),
            ),
            _WORKSPACE,
        )

        assert len(str(payload["title"])) <= 301
        assert len(str(payload["command"])) <= 2_001
        assert len(str(payload["tool_input"]["content"])) <= 2_001  # type: ignore[index]
        paths = payload["paths"]
        assert isinstance(paths, list) and len(paths) == 20
        assert all(len(path) <= 501 for path in paths)

    def test_a_command_that_is_not_text_is_left_in_the_input_only(self) -> None:
        payload = permission_payload(_request(input={"command": ["ls", "-la"]}), _WORKSPACE)

        assert "command" not in payload
        assert payload["tool_input"] == {"command": ["ls", "-la"]}


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'approvals.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
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
    assert pending_permission_answers.waiting_count == 0, "a decision left its request waiting"


@pytest.fixture
def created() -> Iterator[list[ApprovalRecord]]:
    """The approvals the broker opens, in order."""
    create = ApprovalRegistry.create_approval
    records: list[ApprovalRecord] = []

    async def spy(**kwargs: object) -> ApprovalRecord:
        record = await create(**kwargs)
        records.append(record)
        return record

    with patch.object(ApprovalRegistry, "create_approval", spy):
        yield records


def _broker(ttl_seconds: float = 30.0) -> ChatPermissionBroker:
    return ChatPermissionBroker(agent_id="agent-1", chat_id="chat-1", workspace=_WORKSPACE, ttl_seconds=ttl_seconds)


async def _asked(decision: asyncio.Task[PermissionVerdict]) -> ApprovalRecord:
    """The approval the broker opened for the request ``decision`` is deciding."""
    async with asyncio.timeout(5):
        while pending_permission_answers.waiting_count == 0:
            assert not decision.done(), "the broker answered before anybody decided"
            await asyncio.sleep(0.01)
    (record,) = await ApprovalRegistry.list_pending()
    return record


def _resolved_events(bus: MagicMock) -> list[dict[str, object]]:
    return [event.data for (event,), _ in bus.publish.call_args_list if event.event_type == AppEventType.APPROVAL_RESOLVED]


@pytest.mark.usefixtures("bus")
class TestDecide:
    async def test_a_request_becomes_a_pending_approval_of_the_chat(self) -> None:
        decision = asyncio.create_task(_broker(ttl_seconds=30).decide(_request(paths=("/work/repo/a.txt",))))

        record = await _asked(decision)

        assert (record.action_type, record.agent_id, record.chat_id, record.status) == (
            ACTION_TYPE,
            "agent-1",
            "chat-1",
            "PENDING",
        )
        assert record.reason == "claude: Run: ls -la"
        assert record.payload["command"] == "ls -la"
        assert record.expires_at is not None
        assert abs(
            record.expires_at.replace(tzinfo=timezone.utc) - (datetime.now(timezone.utc) + timedelta(seconds=30))
        ) < timedelta(seconds=5)
        await ApprovalRegistry.resolve_approval(record.id, "deny")
        await decision

    @pytest.mark.parametrize(
        ("kind", "severity"),
        [("execute", "high"), ("delete", "high"), ("move", "high"), ("edit", "warning"), ("fetch", "warning")],
    )
    async def test_what_runs_or_removes_things_is_high_risk(self, kind: str, severity: str) -> None:
        decision = asyncio.create_task(_broker().decide(_request(kind=kind)))

        record = await _asked(decision)

        assert record.severity == severity
        await ApprovalRegistry.resolve_approval(record.id, "deny")
        await decision

    async def test_an_approval_lets_the_turn_carry_on(self) -> None:
        decision = asyncio.create_task(_broker().decide(_request()))
        record = await _asked(decision)

        await ApprovalRegistry.resolve_approval(record.id, "approve")

        assert await asyncio.wait_for(decision, timeout=5) == PermissionVerdict(allowed=True)

    async def test_a_denial_refuses_the_request(self) -> None:
        decision = asyncio.create_task(_broker().decide(_request()))
        record = await _asked(decision)

        await ApprovalRegistry.resolve_approval(record.id, "deny")

        assert await asyncio.wait_for(decision, timeout=5) == PermissionVerdict(allowed=False)

    async def test_nobody_answering_in_time_refuses_it_and_says_so(self, bus: MagicMock, created: list[ApprovalRecord]) -> None:
        verdict = await _broker(ttl_seconds=0.1).decide(_request())

        assert verdict == PermissionVerdict(allowed=False, message=NO_ANSWER_MESSAGE)
        assert (await ApprovalRegistry.get_approval(created[0].id)).status == "TIMEOUT"
        assert [(event["action"], event["decision"]) for event in _resolved_events(bus)] == [("withdrawn", "TIMEOUT")]

    async def test_a_turn_that_ends_withdraws_its_request(self, bus: MagicMock) -> None:
        decision = asyncio.create_task(_broker().decide(_request()))
        record = await _asked(decision)

        decision.cancel()
        with pytest.raises(asyncio.CancelledError):
            await decision

        assert (await ApprovalRegistry.get_approval(record.id)).status == "CANCELLED"
        assert [(event["action"], event["decision"]) for event in _resolved_events(bus)] == [("withdrawn", "CANCELLED")]

    async def test_an_answer_given_while_the_request_was_still_being_announced_is_not_lost(self) -> None:
        create = ApprovalRegistry.create_approval

        async def create_and_approve(**kwargs: object) -> ApprovalRecord:
            record = await create(**kwargs)
            await ApprovalRegistry.resolve_approval(record.id, "approve")
            return record

        with patch.object(ApprovalRegistry, "create_approval", create_and_approve):
            verdict = await asyncio.wait_for(_broker().decide(_request()), timeout=5)

        assert verdict == PermissionVerdict(allowed=True)

    async def test_the_reason_for_a_refusal_given_during_the_announcement_is_not_lost_either(self) -> None:
        create = ApprovalRegistry.create_approval

        async def create_and_refuse(**kwargs: object) -> ApprovalRecord:
            record = await create(**kwargs)
            await ApprovalRegistry.resolve_approval(record.id, "deny", comment="Not in this repo")
            return record

        with patch.object(ApprovalRegistry, "create_approval", create_and_refuse):
            verdict = await asyncio.wait_for(_broker().decide(_request()), timeout=5)

        assert verdict == PermissionVerdict(allowed=False, message="Not in this repo")

    async def test_a_request_that_cannot_be_stored_is_not_left_waiting(self) -> None:
        with (
            patch.object(ApprovalRegistry, "create_approval", AsyncMock(side_effect=RuntimeError("database is gone"))),
            pytest.raises(RuntimeError, match="database is gone"),
        ):
            await _broker().decide(_request())

    async def test_a_withdrawal_that_fails_leaves_the_refusal_to_stand(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch.object(ApprovalRegistry, "withdraw", AsyncMock(side_effect=RuntimeError("database is locked"))):
            verdict = await _broker(ttl_seconds=0.05).decide(_request())

        assert verdict == PermissionVerdict(allowed=False, message=NO_ANSWER_MESSAGE)
        assert "external_agent_permission_withdraw_failed" in caplog.text
