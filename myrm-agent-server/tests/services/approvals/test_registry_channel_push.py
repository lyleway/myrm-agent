"""The approval card that ``ApprovalRegistry.create_approval`` pushes to the IM peer of a channel chat."""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.channels.types import ActionButton, OutboundMessage
from app.core.notifications import approval_card
from app.database.models.approval import ApprovalRecord
from app.database.models.base import Base
from app.database.models.chat import Chat
from app.services.approvals.registry import ApprovalRegistry
from app.services.event.app_event_bus import AppEventType

_REASON = "Drop table users in prod"
_SECRET_ARGUMENT = "cat ~/.ssh/id_rsa"
_POINTER = "Open the Myrm AI app to review and decide."


@dataclass(frozen=True)
class _Wires:
    gateway: AsyncMock
    bus: MagicMock
    locale: AsyncMock


@pytest_asyncio.fixture
async def session_factory(tmp_path: Path) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'approvals.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture(autouse=True)
def wires(session_factory: async_sessionmaker[AsyncSession]) -> Iterator[_Wires]:
    @asynccontextmanager
    async def get_session() -> AsyncIterator[AsyncSession]:
        async with session_factory() as session:
            yield session

    wires = _Wires(gateway=AsyncMock(), bus=MagicMock(), locale=AsyncMock(return_value="en"))
    with (
        patch("app.services.approvals.registry.get_session", get_session),
        patch("app.services.approvals.chat_peer_card.get_session", get_session),
        patch("app.services.approvals.registry.get_event_bus", return_value=wires.bus),
        patch("app.core.channel_bridge.get_channel_gateway", return_value=wires.gateway),
        patch("app.core.channel_bridge.locale_provider.resolve_user_locale", wires.locale),
        patch.object(ApprovalRegistry, "_sync_copilot_pending", AsyncMock()),
    ):
        yield wires


async def _seed_chat(session_factory: async_sessionmaker[AsyncSession], *, source: str, session_key: str | None) -> None:
    async with session_factory() as db:
        db.add(Chat(id="chat-1", source=source, channel_session_key=session_key))
        await db.commit()


async def _create(action_type: str = "delete_database", *, status: str = "PENDING") -> ApprovalRecord:
    return await ApprovalRegistry.create_approval(
        agent_id="agent-1",
        action_type=action_type,
        payload={"cmd": _SECRET_ARGUMENT},
        reason=_REASON,
        chat_id="chat-1",
        status=status,
    )


def _pushed(wires: _Wires) -> OutboundMessage:
    wires.gateway.publish.assert_awaited_once()
    message: OutboundMessage = wires.gateway.publish.await_args.args[0]
    return message


def _button_ids(message: OutboundMessage) -> list[str]:
    return [button.action_id for row in message.components for button in row if isinstance(button, ActionButton)]


class TestPushToChannelPeer:
    async def test_a_local_telegram_chat_gets_the_card_with_decision_buttons(
        self, session_factory: async_sessionmaker[AsyncSession], wires: _Wires
    ) -> None:
        await _seed_chat(session_factory, source="telegram", session_key="telegram:user:peer-777")

        record = await _create()

        message = _pushed(wires)
        assert (message.channel, message.recipient_id) == ("telegram", "peer-777")
        assert "**Approval Required: delete_database**" in message.content
        assert f"> {_REASON}" in message.content
        assert _button_ids(message) == [f"approval:approve:{record.id}", f"approval:deny:{record.id}"]

    async def test_the_epoch_suffix_of_the_session_key_is_not_part_of_the_peer(
        self, session_factory: async_sessionmaker[AsyncSession], wires: _Wires
    ) -> None:
        await _seed_chat(session_factory, source="telegram", session_key="telegram:user:peer-777:e=20261121")

        await _create()

        assert _pushed(wires).recipient_id == "peer-777"

    async def test_the_card_is_written_in_the_owners_language(
        self, session_factory: async_sessionmaker[AsyncSession], wires: _Wires
    ) -> None:
        await _seed_chat(session_factory, source="telegram", session_key="telegram:user:peer-777")
        wires.locale.return_value = "zh-CN"

        await _create()

        assert "**需要审批：delete_database**" in _pushed(wires).content

    async def test_a_hosted_chat_gets_a_pointer_because_its_replies_leave_as_text(
        self, session_factory: async_sessionmaker[AsyncSession], wires: _Wires, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed_chat(session_factory, source="telegram", session_key="telegram:user:peer-777")
        monkeypatch.setattr(approval_card, "should_route_via_control_plane", lambda channel: True)

        await _create()

        message = _pushed(wires)
        assert message.components == ()
        assert message.content.endswith(_POINTER)
        assert "approval:" not in message.content

    async def test_a_channel_whose_clicks_are_not_handled_gets_a_pointer(
        self, session_factory: async_sessionmaker[AsyncSession], wires: _Wires
    ) -> None:
        await _seed_chat(session_factory, source="slack", session_key="slack:user:u123")

        await _create()

        message = _pushed(wires)
        assert message.components == ()
        assert message.content.endswith(_POINTER)

    async def test_the_request_payload_never_reaches_the_peer(
        self, session_factory: async_sessionmaker[AsyncSession], wires: _Wires
    ) -> None:
        await _seed_chat(session_factory, source="telegram", session_key="telegram:user:peer-777")

        await _create()

        assert _SECRET_ARGUMENT not in _pushed(wires).content

    async def test_a_held_reply_is_left_to_the_owners_notification(
        self, session_factory: async_sessionmaker[AsyncSession], wires: _Wires
    ) -> None:
        """The chat of a held reply is the sender's: only the owner decides it, through the notification card."""
        await _seed_chat(session_factory, source="telegram", session_key="telegram:user:peer-777")

        await _create("outbound_draft")

        wires.gateway.publish.assert_not_awaited()
        assert wires.bus.publish.call_args.args[0].event_type == AppEventType.APPROVAL_REQUIRED

    async def test_a_web_chat_has_no_peer(self, session_factory: async_sessionmaker[AsyncSession], wires: _Wires) -> None:
        await _seed_chat(session_factory, source="web", session_key=None)

        await _create()

        wires.gateway.publish.assert_not_awaited()

    async def test_an_approval_that_is_not_pending_pushes_nothing(
        self, session_factory: async_sessionmaker[AsyncSession], wires: _Wires
    ) -> None:
        await _seed_chat(session_factory, source="telegram", session_key="telegram:user:peer-777")

        await _create(status="APPROVED")

        wires.gateway.publish.assert_not_awaited()

    async def test_a_failing_push_does_not_fail_the_approval(
        self, session_factory: async_sessionmaker[AsyncSession], wires: _Wires
    ) -> None:
        await _seed_chat(session_factory, source="telegram", session_key="telegram:user:peer-777")
        wires.gateway.publish.side_effect = RuntimeError("platform down")

        record = await _create()

        stored = await ApprovalRegistry.get_approval(record.id)
        assert stored is not None
        assert stored.status == "PENDING"
