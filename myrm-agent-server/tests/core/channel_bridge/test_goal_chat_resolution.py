"""``/goal`` and ``/subgoal`` act on the chat the conversation's next turn is stored in, and a goal set before anything was said is kept.

That chat is the one ``/undo`` resolves: the serving agent's, within the reset epoch the policy puts the message in. A goal
on another chat than the one the work runs in is never seen by the turns that pursue it.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from myrm_agent_harness.toolkits.storage.local import LocalStorageBackend
from sqlalchemy.ext.asyncio import AsyncSession

from app.channels.i18n import get_text
from app.channels.protocols.goal_command import GoalSubcommand, SubgoalSubcommand
from app.channels.types import InboundMessage, SessionPolicy, SessionResetMode
from app.core.channel_bridge.agent_executor import resolve_session_key
from app.core.channel_bridge.command_session import start_conversation
from app.core.channel_bridge.goal_handler import ChannelGoalCommandHandler
from app.core.channel_bridge.goal_handler.chat_resolution import resolve_chat_id
from app.services.agent.gateway import AgentGateway
from app.services.agent.goals.goal_registry import GoalRegistry
from app.services.chat.chat_service import ChatService

from .command_session_testkit import PERSON_KEEPS_ONE_CONVERSATION, PERSON_RESETS_DAILY, stubbed_stores, topic

pytestmark = pytest.mark.usefixtures("session_key_reads_db_session")

_OBJECTIVE = "Ship the report"
_CHAT = ("g1", None)
_MESSAGE = InboundMessage(channel="telegram", sender_id="u1", chat_id="g1", content="/goal", user_id="uid1", is_group=True)
_PERSISTENT = SessionPolicy(mode=SessionResetMode.PERSISTENT)


@pytest.fixture(autouse=True)
def goal_storage(tmp_path: Path) -> Iterator[None]:
    """Goals are kept in files under a scratch directory; no turn is running."""
    gateway = MagicMock(spec=AgentGateway)
    gateway.is_session_active.return_value = False
    gateway.is_session_interrupted.return_value = False
    gateway.get_active_message_id.return_value = None
    gateway.is_draining = False
    with (
        patch("app.platform_utils.get_storage_provider", return_value=LocalStorageBackend(str(tmp_path))),
        patch("app.services.agent.goals.goal_controls.get_agent_gateway", return_value=gateway),
    ):
        yield


async def _chat_under(key: str) -> str:
    return (await ChatService.get_or_create_channel_chat(key, "telegram")).id


async def _stored_chat(key: str) -> str | None:
    chat = await ChatService.get_channel_chat_by_key(key)
    return chat.id if chat else None


# (bindings of the chat, agent that serves the message, agent of the conversation a command could wrongly pick instead)
@pytest.mark.parametrize(
    ("bindings", "serving_agent", "other_agent"),
    [
        pytest.param({}, None, "agent-c", id="no-binding"),
        pytest.param({_CHAT: topic("agent-c")}, "agent-c", None, id="chat-binding-names-agent"),
        pytest.param({_CHAT: topic("agent-c", frozen=True)}, None, "agent-c", id="frozen-identity"),
    ],
)
async def test_a_goal_command_acts_on_the_chat_the_executor_writes_to(
    db_session: AsyncSession,
    bindings: dict[tuple[str, str | None], object],
    serving_agent: str | None,
    other_agent: str | None,
) -> None:
    served = await _chat_under(await resolve_session_key(_MESSAGE, _PERSISTENT, agent_id=serving_agent))
    other = await _chat_under(await resolve_session_key(_MESSAGE, _PERSISTENT, agent_id=other_agent))

    with stubbed_stores(bindings, personal=PERSON_KEEPS_ONE_CONVERSATION):  # type: ignore[arg-type]
        assert await resolve_chat_id(_MESSAGE) == served

    assert served != other


async def test_after_the_daily_reset_the_old_chat_is_not_the_conversation(db_session: AsyncSession) -> None:
    base = await resolve_session_key(_MESSAGE, _PERSISTENT)
    yesterday = await _chat_under(f"{base}:e=20000101")

    with stubbed_stores({}, personal=PERSON_RESETS_DAILY):
        assert await resolve_chat_id(_MESSAGE) is None
        today = await resolve_chat_id(_MESSAGE, create=True)

    assert today not in (None, yesterday)


async def test_a_goal_set_before_anything_was_said_is_kept_on_the_chat_the_first_turn_uses(db_session: AsyncSession) -> None:
    handler = ChannelGoalCommandHandler()
    with stubbed_stores({}, personal=PERSON_KEEPS_ONE_CONVERSATION):
        reply = await handler.handle_goal(_MESSAGE, GoalSubcommand.SET, _OBJECTIVE)
        status = await handler.handle_goal(_MESSAGE, GoalSubcommand.STATUS, "")
        chat_id = await resolve_chat_id(_MESSAGE)

    first_turn_chat = await _chat_under(await resolve_session_key(_MESSAGE, _PERSISTENT))
    assert reply == get_text(_MESSAGE, "goal_set", goal=_OBJECTIVE)
    assert _OBJECTIVE in status
    assert chat_id == first_turn_chat
    stored = await GoalRegistry.resolve_provider(first_turn_chat).get_active_goal(first_turn_chat)
    assert stored is not None
    assert stored.objective == _OBJECTIVE


@pytest.mark.parametrize(
    "subcommand",
    [
        GoalSubcommand.STATUS,
        GoalSubcommand.PAUSE,
        GoalSubcommand.RESUME,
        GoalSubcommand.CLEAR,
        GoalSubcommand.BUDGET,
        GoalSubcommand.CONSTRAINT,
        GoalSubcommand.WAIT,
        GoalSubcommand.UNWAIT,
    ],
)
async def test_a_goal_command_that_sets_nothing_opens_no_chat(db_session: AsyncSession, subcommand: GoalSubcommand) -> None:
    with stubbed_stores({}, personal=PERSON_KEEPS_ONE_CONVERSATION):
        await ChannelGoalCommandHandler().handle_goal(_MESSAGE, subcommand, "3")
        await ChannelGoalCommandHandler().handle_subgoal(_MESSAGE, SubgoalSubcommand.LIST, "")

    assert await _stored_chat(await resolve_session_key(_MESSAGE, _PERSISTENT)) is None


async def test_after_new_a_goal_command_acts_on_the_new_conversation_and_not_on_the_ended_one(db_session: AsyncSession) -> None:
    ended = await _chat_under(await resolve_session_key(_MESSAGE, _PERSISTENT))
    await ChatService.append_message(ended, "user", "hello", datetime.now(UTC), "UTC", message_id="m-ended")
    await GoalRegistry.resolve_provider(ended).create_goal(ended, _OBJECTIVE)
    handler = ChannelGoalCommandHandler()

    with stubbed_stores({}, personal=PERSON_KEEPS_ONE_CONVERSATION):
        before = await handler.handle_goal(_MESSAGE, GoalSubcommand.STATUS, "")
        await start_conversation(_MESSAGE)
        after = await handler.handle_goal(_MESSAGE, GoalSubcommand.STATUS, "")
        reply = await handler.handle_goal(_MESSAGE, GoalSubcommand.SET, "Another goal")
        new_chat = await resolve_chat_id(_MESSAGE)

    assert _OBJECTIVE in before
    assert after == get_text(_MESSAGE, "no_goal_is_set")
    assert reply == get_text(_MESSAGE, "goal_set", goal="Another goal")
    assert new_chat not in (None, ended)
    kept = await GoalRegistry.resolve_provider(new_chat).get_latest_goal(new_chat)
    assert kept is not None
    assert kept.objective == "Another goal"
    stored = await GoalRegistry.resolve_provider(ended).get_latest_goal(ended)
    assert stored is not None
    assert stored.objective == _OBJECTIVE
