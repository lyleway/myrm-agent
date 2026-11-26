"""``channel_key_extends``, ``channel_key_epochs`` and ``channel_key_started`` select a conversation's keys at the segment boundary."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import ColumnElement, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.types import InboundMessage, SessionPolicy, SessionResetMode, started_session_key
from app.core.channel_bridge.agent_executor.session import _build_session_key, resolve_session_key
from app.database.models.chat import Chat
from app.database.repositories.channel_key_match import channel_key_epochs, channel_key_extends, channel_key_started
from app.services.chat.handoff import _build_target_session_key
from tests.support.channel_chat_rows import seed_channel_chat

pytestmark = pytest.mark.usefixtures("session_key_reads_db_session")

_BASE = "telegram:dm:u1"
_STARTED = [
    f"{_BASE}:e=s20260101T000000",
    f"{_BASE}:e=s20261231T235959",
]
_OWN = [
    _BASE,
    f"{_BASE}:e=2026-01-01",
    f"{_BASE}:e=20260101T0000",
]
_CONTINUATIONS = [
    f"{_BASE}:thread:t1",
    f"{_BASE}:thread:t1:e=2026-01-01",
    f"{_BASE}:agent:a1:e=20260101T0000",
    f"{_BASE}:agent:a1:e=s20260101T000000",
    f"{_BASE}:thread:t1:e=s20260101T000000",
]
_OTHERS = [
    "telegram:dm:u10:e=2026-01-01",
    "telegram:dm:u10:e=s20260101T000000",
    "telegram:dm:u1x",
    "telegram:dm:u",
    "telegram:dm:u:e=s20260101T000000",
    "slack:dm:u1",
    "slack:dm:u1:e=s20260101T000000",
]


async def _matched(db: AsyncSession, match: ColumnElement[bool]) -> set[str]:
    keys = (await db.execute(select(Chat.channel_session_key).where(match))).scalars()
    return {key for key in keys if key is not None}


async def _seed_all(db: AsyncSession) -> None:
    for key in [*_STARTED, *_OWN, *_CONTINUATIONS, *_OTHERS]:
        await seed_channel_chat(db, key)


@pytest.mark.asyncio
async def test_extends_matches_the_key_and_every_continuation_of_it(db_session: AsyncSession) -> None:
    await _seed_all(db_session)

    assert await _matched(db_session, channel_key_extends(_BASE)) == {*_STARTED, *_OWN, *_CONTINUATIONS}


@pytest.mark.asyncio
async def test_epochs_match_the_key_and_its_epoch_keys_only(db_session: AsyncSession) -> None:
    await _seed_all(db_session)

    assert await _matched(db_session, channel_key_epochs(_BASE)) == {*_STARTED, *_OWN}


@pytest.mark.asyncio
async def test_started_matches_the_conversations_opened_on_request_and_nothing_else(db_session: AsyncSession) -> None:
    await _seed_all(db_session)

    assert await _matched(db_session, channel_key_started(_BASE)) == set(_STARTED)


@pytest.mark.asyncio
@pytest.mark.parametrize("match", [channel_key_extends, channel_key_epochs])
async def test_an_underscore_matches_only_an_underscore(
    db_session: AsyncSession, match: Callable[[str], ColumnElement[bool]]
) -> None:
    await seed_channel_chat(db_session, "telegram:dm:u_1:e=2026-01-01")
    await seed_channel_chat(db_session, "telegram:dm:ux1:e=2026-01-01")

    assert await _matched(db_session, match("telegram:dm:u_1")) == {"telegram:dm:u_1:e=2026-01-01"}


@pytest.mark.asyncio
async def test_an_underscore_in_the_peer_matches_only_its_own_started_keys(db_session: AsyncSession) -> None:
    await seed_channel_chat(db_session, "telegram:dm:u_1:e=s20260101T000000")
    await seed_channel_chat(db_session, "telegram:dm:ux1:e=s20260101T000000")

    assert await _matched(db_session, channel_key_started("telegram:dm:u_1")) == {"telegram:dm:u_1:e=s20260101T000000"}


@pytest.mark.asyncio
async def test_the_greatest_started_key_is_the_newest_conversation(db_session: AsyncSession) -> None:
    keys = [f"{_BASE}:e=s20260101T235959", f"{_BASE}:e=s20260102T000000", f"{_BASE}:e=s20251231T235959"]
    for key in keys:
        await seed_channel_chat(db_session, key)

    newest = (
        await db_session.execute(
            select(Chat.channel_session_key).where(channel_key_started(_BASE)).order_by(Chat.channel_session_key.desc()).limit(1)
        )
    ).scalar_one()

    assert newest == f"{_BASE}:e=s20260102T000000"


@pytest.mark.asyncio
async def test_the_keys_the_resolver_and_the_handoff_write_are_matched(db_session: AsyncSession) -> None:
    """The matches read the key format the resolver and the handoff write, so they have to follow any change to it."""
    msg = InboundMessage(channel="telegram", sender_id="u1", content="hi")
    base = _build_session_key(msg)
    opened = started_session_key(base, datetime(2026, 1, 1, 12, tzinfo=UTC))
    handed = _build_target_session_key("telegram", "u1")
    written = {
        await resolve_session_key(msg, SessionPolicy(mode=SessionResetMode.PERSISTENT)),
        await resolve_session_key(msg, SessionPolicy(mode=SessionResetMode.DAILY)),
        opened,
        handed,
    }
    for key in written:
        await seed_channel_chat(db_session, key)

    assert await _matched(db_session, channel_key_epochs(base)) == written
    assert await _matched(db_session, channel_key_started(base)) == {opened, handed}
