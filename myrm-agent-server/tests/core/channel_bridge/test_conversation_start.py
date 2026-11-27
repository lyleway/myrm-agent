"""``/new`` opens a conversation that commands and turns keep resolving to, under every reset policy.

The conversation is stored when ``/new`` runs, under a key no reset policy derives, so whatever follows (a command, a turn
at once, a turn hours later, a handoff, the button that continues a reply in the browser, a peer next door) resolves
against stored rows and not against a marker waiting in memory. The reset policy decides only how long it lasts. These
tests run the real resolver, command resolution and chat service over a real database.

Every database costs seconds to build, so a test that holds for each reset policy runs the policies side by side in one
database, one peer apiece (peers never share a conversation).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.routing.pipeline import RouterDeliveryMixin
from app.channels.types import ActionButton, InboundMessage, OutboundMessage, is_started_session_key, started_session_key
from app.core.channel_bridge.agent_executor.session import build_channel_session_key
from app.core.channel_bridge.command_session import resolve_command_session_key, start_conversation
from app.database.models.chat import Chat
from app.database.repositories.channel_key_match import channel_key_extends
from app.services.chat.chat_service import ChatService
from app.services.chat.handoff import _build_target_session_key
from tests.support.channel_chat_rows import seed_channel_chat, seed_costed_reply

from .command_session_testkit import stubbed_stores, topic

pytestmark = pytest.mark.usefixtures("session_key_reads_db_session")

_CLOCK = "app.core.channel_bridge.command_session.datetime"
_PERSISTENT: dict[str, object] = {"sessionPolicy": {"mode": "persistent"}}
_PEOPLE: dict[str, dict[str, object]] = {
    "persistent": _PERSISTENT,
    "daily": {"sessionPolicy": {"mode": "daily", "dailyResetHour": 4}},
    "idle": {"sessionPolicy": {"mode": "idle", "idleMinutes": 120}},
}
_ME = "u1"


def _dm(sender_id: str = _ME) -> InboundMessage:
    return InboundMessage(channel="telegram", sender_id=sender_id, content="/new", user_id="uid1")


def _base(sender_id: str = _ME) -> str:
    return build_channel_session_key(_dm(sender_id))


async def _converse(db: AsyncSession, key: str, *, idle_minutes: float = 0.0) -> str:
    """Store a chat under ``key`` that holds a turn and was last active ``idle_minutes`` ago; return its id."""
    chat_id = await seed_channel_chat(db, key, idle_minutes=idle_minutes)
    await seed_costed_reply(db, chat_id, 0.0)
    return chat_id


async def _speak_in(db: AsyncSession, key: str) -> None:
    chat = await ChatService.get_channel_chat_by_key(key)
    assert chat is not None
    await seed_costed_reply(db, chat.id, 0.0)


async def _stored_keys(db: AsyncSession, base: str = _base()) -> list[str]:
    keys = (await db.execute(select(Chat.channel_session_key).where(channel_key_extends(base)))).scalars()
    return sorted(key for key in keys if key is not None)


# --- what /new stores -------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_after_new_every_turn_and_command_resolves_to_the_new_conversation(db_session: AsyncSession) -> None:
    for name, person in _PEOPLE.items():
        msg, base = _dm(f"u-{name}"), _base(f"u-{name}")
        with stubbed_stores({}, personal=person):
            ended_key = await resolve_command_session_key(msg)
            ended_id = await _converse(db_session, ended_key)

            await start_conversation(msg)

            resolved = {await resolve_command_session_key(msg) for _ in range(3)}

        [started_key] = resolved
        assert started_key.startswith(f"{base}:e=s") and is_started_session_key(started_key), name
        started = await ChatService.get_channel_chat_by_key(started_key)
        assert started is not None and started.id != ended_id and started.source == "telegram", name
        ended = await ChatService.get_channel_chat_by_key(ended_key)
        assert ended is not None and ended.id == ended_id, f"{name}: the ended conversation stays stored"


@pytest.mark.asyncio
async def test_new_on_a_chat_that_has_not_spoken_opens_nothing(db_session: AsyncSession) -> None:
    for name, person in _PEOPLE.items():
        msg, base = _dm(f"u-{name}"), _base(f"u-{name}")
        with stubbed_stores({}, personal=person):
            await start_conversation(msg)
            assert await _stored_keys(db_session, base) == [], f"{name}: no chat yet, nothing to end"

            key = await resolve_command_session_key(msg)
            await seed_channel_chat(db_session, key)
            await start_conversation(msg)
            await start_conversation(msg)

            assert await resolve_command_session_key(msg) == key, name
        assert await _stored_keys(db_session, base) == [key], name


@pytest.mark.asyncio
async def test_each_new_after_a_turn_opens_the_next_conversation(db_session: AsyncSession) -> None:
    first = datetime(2026, 1, 1, 12, tzinfo=UTC)
    second = first + timedelta(seconds=1)
    with stubbed_stores({}, personal=_PERSISTENT), patch(_CLOCK) as clock:
        clock.now.side_effect = [first, second]
        await _converse(db_session, await resolve_command_session_key(_dm()))

        await start_conversation(_dm())
        first_key = await resolve_command_session_key(_dm())
        await _speak_in(db_session, first_key)
        await start_conversation(_dm())
        second_key = await resolve_command_session_key(_dm())

    assert first_key == started_session_key(_base(), first)
    assert second_key == started_session_key(_base(), second)


@pytest.mark.asyncio
async def test_a_second_new_within_the_same_second_reuses_the_conversation_instead_of_failing(db_session: AsyncSession) -> None:
    moment = datetime(2026, 1, 1, 12, tzinfo=UTC)
    with stubbed_stores({}, personal=_PERSISTENT), patch(_CLOCK) as clock:
        clock.now.return_value = moment
        await _converse(db_session, await resolve_command_session_key(_dm()))
        await start_conversation(_dm())
        key = await resolve_command_session_key(_dm())
        await _speak_in(db_session, key)

        await start_conversation(_dm())

        assert await resolve_command_session_key(_dm()) == key
    assert await _stored_keys(db_session) == sorted([_base(), key])


@pytest.mark.asyncio
async def test_a_conversation_that_could_not_be_stored_leaves_the_current_one_in_place(db_session: AsyncSession) -> None:
    with (
        stubbed_stores({}, personal=_PERSISTENT),
        patch.object(ChatService, "get_or_create_channel_chat", AsyncMock(side_effect=RuntimeError("db down"))),
    ):
        current = await resolve_command_session_key(_dm())
        await _converse(db_session, current)

        with pytest.raises(RuntimeError, match="db down"):
            await start_conversation(_dm())

        assert await resolve_command_session_key(_dm()) == current


@pytest.mark.asyncio
async def test_the_conversation_is_opened_for_the_agent_that_serves_the_chat(db_session: AsyncSession) -> None:
    group = InboundMessage(channel="telegram", sender_id="u1", chat_id="g1", content="/new", user_id="uid1", is_group=True)
    with stubbed_stores({("g1", None): topic("agent-c")}, personal=_PERSISTENT):
        await _converse(db_session, await resolve_command_session_key(group))

        await start_conversation(group)

        key = await resolve_command_session_key(group)
    assert key.startswith("telegram:group:g1:agent:agent-c:e=s")
    started = await ChatService.get_channel_chat_by_key(key)
    assert started is not None and started.agent_id == "agent-c"


@pytest.mark.asyncio
async def test_the_browser_button_of_a_reply_links_the_conversation_opened_by_new(db_session: AsyncSession) -> None:
    """Under every reset policy the button links the conversation the next turn writes to, not the ended one."""

    async def button_to(chat_id: str, *, locale: str) -> tuple[tuple[ActionButton, ...], ...]:
        return ((ActionButton(label="Continue", action_id="web:continue_chat", url=f"https://app.test/{chat_id}"),),)

    for name, person in _PEOPLE.items():
        msg = _dm(f"u-{name}")
        reply = OutboundMessage(channel="telegram", recipient_id=msg.sender_id, content="done", user_id="uid1")
        with (
            stubbed_stores({}, personal=person),
            patch("app.remote_access.mobile_deep_link.resolve_web_handoff_components", button_to),
        ):
            ended_id = await _converse(db_session, await resolve_command_session_key(msg))
            await start_conversation(msg)
            started = await ChatService.get_channel_chat_by_key(await resolve_command_session_key(msg))

            delivered = await RouterDeliveryMixin._attach_web_handoff(reply, msg)

        assert started is not None and started.id != ended_id, name
        assert delivered.components == await button_to(started.id, locale="en"), name


# --- how long it lasts -------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_persistent_policy_keeps_the_conversation_until_another_is_opened(db_session: AsyncSession) -> None:
    key = started_session_key(_base(), datetime.now(UTC) - timedelta(days=400))
    await seed_channel_chat(db_session, key, idle_minutes=400 * 24 * 60)

    with stubbed_stores({}, personal=_PERSISTENT):
        assert await resolve_command_session_key(_dm()) == key


@pytest.mark.asyncio
async def test_a_daily_reset_ends_the_conversation_opened_before_it(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    reset_hour = (now.hour + 12) % 24  # the next reset is hours away, so the epoch cannot change during the test
    person: dict[str, object] = {"sessionPolicy": {"mode": "daily", "dailyResetHour": reset_hour}}
    today = started_session_key(_base(), now)

    await seed_channel_chat(db_session, started_session_key(_base(), now - timedelta(days=2)))
    with stubbed_stores({}, personal=person):
        ended_by_the_reset = await resolve_command_session_key(_dm())
        await seed_channel_chat(db_session, today)
        within_the_epoch = await resolve_command_session_key(_dm())

    assert ended_by_the_reset.startswith(f"{_base()}:e=") and not is_started_session_key(ended_by_the_reset)
    assert within_the_epoch == today


@pytest.mark.asyncio
async def test_an_idle_policy_ends_the_conversation_after_the_idle_window(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    kept = started_session_key(_base("u-inside"), now - timedelta(minutes=10))
    ended = started_session_key(_base("u-past"), now - timedelta(minutes=180))
    await seed_channel_chat(db_session, kept, idle_minutes=10)
    await seed_channel_chat(db_session, ended, idle_minutes=180)

    with stubbed_stores({}, personal=_PEOPLE["idle"]):
        inside_the_window = await resolve_command_session_key(_dm("u-inside"))
        past_the_window = await resolve_command_session_key(_dm("u-past"))

    assert inside_the_window == kept
    assert past_the_window != ended and past_the_window.startswith(f"{_base('u-past')}:e=")


@pytest.mark.asyncio
async def test_a_late_write_to_an_older_conversation_does_not_bring_it_back(db_session: AsyncSession) -> None:
    """The ended conversation was written to a minute ago; the one opened after it, fifty minutes ago, is still the one."""
    now = datetime.now(UTC)
    expected: dict[str, str] = {}
    for name in ("persistent", "idle"):
        await seed_channel_chat(db_session, _base(f"u-{name}"), idle_minutes=1)
        expected[name] = started_session_key(_base(f"u-{name}"), now - timedelta(minutes=50))
        await seed_channel_chat(db_session, expected[name], idle_minutes=50)

    for name, key in expected.items():
        with stubbed_stores({}, personal=_PEOPLE[name]):
            assert await resolve_command_session_key(_dm(f"u-{name}")) == key, name


@pytest.mark.asyncio
async def test_the_newest_of_several_opened_conversations_is_the_one_continued(db_session: AsyncSession) -> None:
    now = datetime.now(UTC)
    newest = started_session_key(_base(), now - timedelta(hours=1))
    for key in (
        started_session_key(_base(), now - timedelta(hours=3)),
        newest,
        started_session_key(_base(), now - timedelta(hours=2)),
    ):
        await seed_channel_chat(db_session, key)

    with stubbed_stores({}, personal=_PERSISTENT):
        assert await resolve_command_session_key(_dm()) == newest


@pytest.mark.asyncio
async def test_a_conversation_opened_by_another_peer_is_not_continued(db_session: AsyncSession) -> None:
    """Keys that start with the peer's own characters, far in the future so that they sort last, belong to others."""
    for neighbour in (
        "telegram:dm:u10:e=s20991231T235959",
        "telegram:dm:u1:agent:a1:e=s20991231T235959",
        "telegram:dm:u1:thread:t1:e=s20991231T235959",
        "slack:dm:u1:e=s20991231T235959",
    ):
        await seed_channel_chat(db_session, neighbour)

    with stubbed_stores({}, personal=_PERSISTENT):
        assert await resolve_command_session_key(_dm()) == _base()


# --- /handoff opens conversations the same way -------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_handed_off_chat_is_the_conversation_of_the_target_peer(db_session: AsyncSession) -> None:
    handed: dict[str, str] = {}
    for name in _PEOPLE:
        handed[name] = _build_target_session_key("telegram", f"u-{name}")
        await seed_channel_chat(db_session, handed[name])

    for name, person in _PEOPLE.items():
        with stubbed_stores({}, personal=person):
            assert await resolve_command_session_key(_dm(f"u-{name}")) == handed[name], name


@pytest.mark.asyncio
async def test_a_handoff_after_new_is_the_newer_conversation(db_session: AsyncSession) -> None:
    await seed_channel_chat(db_session, started_session_key(_base(), datetime.now(UTC) - timedelta(minutes=5)))
    handed = _build_target_session_key("telegram", _ME)
    await seed_channel_chat(db_session, handed)

    with stubbed_stores({}, personal=_PERSISTENT):
        assert await resolve_command_session_key(_dm()) == handed
