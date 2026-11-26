"""Unit tests for cross-platform session handoff service.

Covers: handoff_chat, _build_target_session_key
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.channels.types import is_started_session_key, started_session_key
from app.services.chat.handoff import HandoffResult, _build_target_session_key, handoff_chat

_MOMENT = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

# ─── _build_target_session_key ──────────────────────────────────


class TestBuildTargetSessionKey:
    """The handed chat is stored as a conversation opened on the target peer, whatever the peer's reset policy is."""

    def test_the_key_is_a_conversation_opened_now_on_the_target_peer(self) -> None:
        with patch("app.services.chat.handoff.datetime") as clock:
            clock.now.return_value = _MOMENT
            key = _build_target_session_key("telegram", "user123")
        assert key == "telegram:dm:user123:e=s20260101T120000"
        assert key == started_session_key("telegram:dm:user123", _MOMENT)

    def test_the_agent_of_the_chat_is_part_of_the_key(self) -> None:
        key = _build_target_session_key("telegram", "u1", agent_id="agent-x")
        assert key.startswith("telegram:dm:u1:agent:agent-x:e=s")
        assert is_started_session_key(key)

    def test_a_peer_id_is_sanitised_the_way_the_resolver_does(self) -> None:
        assert _build_target_session_key("slack", "Peer 2!").startswith("slack:dm:peer_2_:e=s")


# ─── handoff_chat ───────────────────────────────────────────────


def _make_mock_channel(connected: bool = True) -> MagicMock:
    ch = MagicMock()
    ch.is_connected = connected
    return ch


async def _seed_chat(
    session_factory_fn: Callable[[], async_sessionmaker[AsyncSession]],
    chat_id: str,
    source: str = "web",
    channel_session_key: str | None = None,
    agent_id: str | None = None,
) -> None:
    from app.database.models.chat import Chat

    factory = session_factory_fn()
    async with factory() as db:
        chat = Chat(
            id=chat_id,
            title=f"Test {chat_id[:8]}",
            source=source,
            channel_session_key=channel_session_key,
            agent_id=agent_id,
            created_at=datetime.now(UTC),
            updated_at=datetime.now(UTC),
        )
        db.add(chat)
        await db.commit()


async def _seed_pairing(
    session_factory_fn: Callable[[], async_sessionmaker[AsyncSession]],
    channel: str,
    sender_id: str,
    status: str = "active",
) -> None:
    from app.database.models.channel import ChannelPairingModel

    factory = session_factory_fn()
    async with factory() as db:
        pairing = ChannelPairingModel(
            id=uuid.uuid4().hex[:32],
            channel=channel,
            sender_id=sender_id,
            status=status,
        )
        db.add(pairing)
        await db.commit()


_GATEWAY_PATCH = "app.core.channel_bridge.channel_gateway"


@pytest.mark.asyncio
async def test_handoff_chat_not_found() -> None:
    with patch(_GATEWAY_PATCH) as mock_gw:
        mock_gw.bus.get_channel.return_value = _make_mock_channel()
        result = await handoff_chat("nonexistent-id", "telegram")
        assert not result.success
        assert "not found" in result.error.lower()


@pytest.mark.asyncio
async def test_handoff_channel_not_found() -> None:
    from app.platform_utils import get_session_factory

    chat_id = str(uuid.uuid4())
    await _seed_chat(get_session_factory, chat_id)

    with patch(_GATEWAY_PATCH) as mock_gw:
        mock_gw.bus.get_channel.return_value = None
        result = await handoff_chat(chat_id, "nonexistent_channel")
        assert not result.success
        assert "not found" in result.error.lower()
        assert result.target_channel == "nonexistent_channel"


@pytest.mark.asyncio
async def test_handoff_channel_not_connected() -> None:
    from app.platform_utils import get_session_factory

    chat_id = str(uuid.uuid4())
    await _seed_chat(get_session_factory, chat_id)

    with patch(_GATEWAY_PATCH) as mock_gw:
        mock_gw.bus.get_channel.return_value = _make_mock_channel(connected=False)
        result = await handoff_chat(chat_id, "telegram")
        assert not result.success
        assert "not connected" in result.error.lower()


@pytest.mark.asyncio
async def test_handoff_same_channel() -> None:
    from app.platform_utils import get_session_factory

    chan = f"tg_same_{uuid.uuid4().hex[:8]}"
    chat_id = str(uuid.uuid4())
    await _seed_chat(
        get_session_factory,
        chat_id,
        source=chan,
        channel_session_key=f"{chan}:dm:u1",
    )

    with patch(_GATEWAY_PATCH) as mock_gw:
        mock_gw.bus.get_channel.return_value = _make_mock_channel()
        result = await handoff_chat(chat_id, chan)
        assert not result.success
        assert "already on this channel" in result.error.lower()


@pytest.mark.asyncio
async def test_handoff_no_pairing() -> None:
    from app.platform_utils import get_session_factory

    chan = f"tg_nopair_{uuid.uuid4().hex[:8]}"
    chat_id = str(uuid.uuid4())
    await _seed_chat(get_session_factory, chat_id, source="web")

    with patch(_GATEWAY_PATCH) as mock_gw:
        mock_gw.bus.get_channel.return_value = _make_mock_channel()
        result = await handoff_chat(chat_id, chan)
        assert not result.success
        assert "pairing" in result.error.lower()


@pytest.mark.asyncio
async def test_handoff_success() -> None:
    from app.platform_utils import get_session_factory

    chan = f"tg_ok_{uuid.uuid4().hex[:8]}"
    chat_id = str(uuid.uuid4())
    await _seed_chat(get_session_factory, chat_id, source="web")
    await _seed_pairing(get_session_factory, chan, "tg_user_123")

    with patch(_GATEWAY_PATCH) as mock_gw:
        mock_gw.bus.get_channel.return_value = _make_mock_channel()
        result = await handoff_chat(chat_id, chan)
        assert result.success
        assert result.target_channel == chan
        assert result.target_session_key.startswith(f"{chan}:dm:tg_user_123:e=s")
        assert is_started_session_key(result.target_session_key)

    factory = get_session_factory()
    async with factory() as db:
        from sqlalchemy import select

        from app.database.models.chat import Chat

        chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one()
        assert chat.source == chan
        assert chat.channel_session_key == result.target_session_key


@pytest.mark.asyncio
async def test_handoff_resolves_unique_conflict() -> None:
    """When target_key is already occupied by another chat, the old chat gets unbound."""
    from app.platform_utils import get_session_factory

    chan = f"fs_conflict_{uuid.uuid4().hex[:8]}"
    target_key = started_session_key(f"{chan}:dm:fs_user_1", _MOMENT)

    old_chat_id = str(uuid.uuid4())
    await _seed_chat(
        get_session_factory,
        old_chat_id,
        source=chan,
        channel_session_key=target_key,
    )

    new_chat_id = str(uuid.uuid4())
    await _seed_chat(get_session_factory, new_chat_id, source="web")
    await _seed_pairing(get_session_factory, chan, "fs_user_1")

    with patch(_GATEWAY_PATCH) as mock_gw, patch("app.services.chat.handoff.datetime") as clock:
        mock_gw.bus.get_channel.return_value = _make_mock_channel()
        clock.now.return_value = _MOMENT
        result = await handoff_chat(new_chat_id, chan)
        assert result.success
        assert result.target_session_key == target_key

    factory = get_session_factory()
    async with factory() as db:
        from sqlalchemy import select

        from app.database.models.chat import Chat

        old_chat = (await db.execute(select(Chat).where(Chat.id == old_chat_id))).scalar_one()
        assert old_chat.channel_session_key is None

        new_chat = (await db.execute(select(Chat).where(Chat.id == new_chat_id))).scalar_one()
        assert new_chat.channel_session_key == target_key
        assert new_chat.source == chan


@pytest.mark.asyncio
async def test_handoff_preserves_agent_id() -> None:
    """Handoff with agent_id should produce a target_key containing :agent:xxx."""
    from app.platform_utils import get_session_factory

    chan = f"slack_agent_{uuid.uuid4().hex[:8]}"
    chat_id = str(uuid.uuid4())
    await _seed_chat(get_session_factory, chat_id, source="web", agent_id="skill-writer")
    await _seed_pairing(get_session_factory, chan, "slack_peer_1")

    with patch(_GATEWAY_PATCH) as mock_gw:
        mock_gw.bus.get_channel.return_value = _make_mock_channel()
        result = await handoff_chat(chat_id, chan)
        assert result.success
        assert ":agent:skill-writer:e=s" in result.target_session_key


@pytest.mark.asyncio
async def test_handoff_inactive_pairing_rejected() -> None:
    """Pairing with status != 'active' should be treated as missing."""
    from app.platform_utils import get_session_factory

    chan = f"dc_inactive_{uuid.uuid4().hex[:8]}"
    chat_id = str(uuid.uuid4())
    await _seed_chat(get_session_factory, chat_id, source="web")
    await _seed_pairing(get_session_factory, chan, "dc_user_1", status="inactive")

    with patch(_GATEWAY_PATCH) as mock_gw:
        mock_gw.bus.get_channel.return_value = _make_mock_channel()
        result = await handoff_chat(chat_id, chan)
        assert not result.success
        assert "pairing" in result.error.lower()


@pytest.mark.asyncio
async def test_handoff_same_source_but_no_key_allowed() -> None:
    """source == target but channel_session_key is None should allow handoff."""
    from app.platform_utils import get_session_factory

    chan = f"tg_nokey_{uuid.uuid4().hex[:8]}"
    chat_id = str(uuid.uuid4())
    await _seed_chat(get_session_factory, chat_id, source=chan, channel_session_key=None)
    await _seed_pairing(get_session_factory, chan, "tg_rehandoff")

    with patch(_GATEWAY_PATCH) as mock_gw:
        mock_gw.bus.get_channel.return_value = _make_mock_channel()
        result = await handoff_chat(chat_id, chan)
        assert result.success
        assert result.target_session_key.startswith(f"{chan}:dm:tg_rehandoff:e=s")


@pytest.mark.asyncio
async def test_handoff_same_source_mismatched_key_allowed() -> None:
    """source == target but key points to a different channel should allow handoff."""
    from app.platform_utils import get_session_factory

    chan = f"tg_mismatch_{uuid.uuid4().hex[:8]}"
    chat_id = str(uuid.uuid4())
    await _seed_chat(
        get_session_factory,
        chat_id,
        source=chan,
        channel_session_key="web:dm:old_user",
    )
    await _seed_pairing(get_session_factory, chan, "tg_mismatch_user")

    with patch(_GATEWAY_PATCH) as mock_gw:
        mock_gw.bus.get_channel.return_value = _make_mock_channel()
        result = await handoff_chat(chat_id, chan)
        assert result.success
        assert result.target_session_key.startswith(f"{chan}:dm:tg_mismatch_user:e=s")


@pytest.mark.asyncio
async def test_handoff_result_dataclass() -> None:
    r = HandoffResult(success=True, target_channel="tg", target_session_key="tg:dm:u1")
    assert r.success
    assert r.error == ""
    assert r.target_channel == "tg"
    assert r.target_session_key == "tg:dm:u1"

    r2 = HandoffResult(success=False, error="boom")
    assert not r2.success
    assert r2.error == "boom"
