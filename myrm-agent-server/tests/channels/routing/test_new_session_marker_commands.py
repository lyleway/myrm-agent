"""``/new`` ends the conversation for every command at once, and only the turn that starts the next one spends the marker.

Locally the marker waits in the router. Hosted, the control plane spends its own marker on the first message it forwards,
a command included, and sends the next turn without the flag. Either way a command in between acts on no conversation,
and the next turn starts exactly one new one.
"""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.routing.command_defs import CommandAction
from app.channels.routing.router import AgentRouter
from app.channels.types import InboundMessage

_NEW = "force_new_epoch"


def _msg(content: str, *, flagged: bool = False) -> InboundMessage:
    """A DM the control plane forwarded: the router takes the identity it carries as resolved."""
    metadata: dict[str, object] = {"trusted_inbound": "control_plane"}
    if flagged:
        metadata[_NEW] = True
    return InboundMessage(channel="test", sender_id="u1", chat_id="c1", content=content, user_id="test-user", metadata=metadata)


@pytest.fixture
def router() -> AgentRouter:
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    router = AgentRouter(bus, MagicMock(), MagicMock())
    router._resolve_topic = AsyncMock(return_value=None)  # type: ignore[method-assign]
    return router


async def _starts_new_conversation(router: AgentRouter, *texts: str) -> list[object]:
    """For each turn said in order: whether it starts a new conversation."""
    flags: list[object] = []
    for text in texts:
        ctx = await router._prepare_execution_context(_msg(text))
        assert ctx is not None
        flags.append(ctx.exec_msg.metadata.get(_NEW))
    return flags


async def test_a_command_after_new_acts_on_no_conversation_and_the_next_turn_still_starts_one(router: AgentRouter) -> None:
    await router._handle_new_session(_msg("/new"))
    command = _msg("/undo")

    await router._dispatch_system_command(command, CommandAction.UNDO, "")

    assert command.metadata.get(_NEW) is True
    assert await _starts_new_conversation(router, "hello", "again") == [True, None]


async def test_a_marker_the_control_plane_spent_on_a_command_is_set_again_for_the_next_turn(router: AgentRouter) -> None:
    command = _msg("/undo", flagged=True)

    await router._dispatch_system_command(command, CommandAction.UNDO, "")

    assert await _starts_new_conversation(router, "hello", "again") == [True, None]


async def test_commands_and_turns_without_a_new_carry_no_flag(router: AgentRouter) -> None:
    command = _msg("/undo")

    await router._dispatch_system_command(command, CommandAction.UNDO, "")

    assert _NEW not in command.metadata
    assert await _starts_new_conversation(router, "hello") == [None]


async def test_several_commands_before_the_first_turn_start_one_conversation(router: AgentRouter) -> None:
    await router._handle_new_session(_msg("/new"))
    commands = [
        (_msg("/status", flagged=True), CommandAction.STATUS),
        (_msg("/undo"), CommandAction.UNDO),
        (_msg("/compact"), CommandAction.COMPACT),
    ]

    for command, action in commands:
        await router._dispatch_system_command(command, action, "")

    assert [command.metadata.get(_NEW) for command, _ in commands] == [True, True, True]
    assert await _starts_new_conversation(router, "hello", "again", "more") == [True, None, None]


async def test_a_command_that_becomes_a_turn_starts_the_new_conversation_once(router: AgentRouter) -> None:
    await router._handle_new_session(_msg("/new"))
    command = _msg("/goal ship it")
    await router._dispatch_system_command(command, CommandAction.GOAL, "ship it")
    kickoff = dataclasses.replace(command, content="Work toward: ship it")

    ctx = await router._prepare_execution_context(kickoff)

    assert ctx is not None
    assert ctx.exec_msg.metadata.get(_NEW) is True
    assert await _starts_new_conversation(router, "hello") == [None]


@pytest.mark.parametrize(
    ("action", "handler"),
    [
        pytest.param(CommandAction.UNDO, "_handle_undo", id="undo"),
        pytest.param(CommandAction.RETRY, "_handle_retry", id="retry"),
        pytest.param(CommandAction.COMPACT, "_handle_compact", id="compact"),
        pytest.param(CommandAction.HANDOFF, "_handle_handoff_command", id="handoff"),
        pytest.param(CommandAction.GOAL, "_handle_goal_command", id="goal"),
        pytest.param(CommandAction.SUBGOAL, "_handle_subgoal_command", id="subgoal"),
    ],
)
async def test_every_command_that_acts_on_the_conversation_receives_the_flag(
    router: AgentRouter, monkeypatch: pytest.MonkeyPatch, action: CommandAction, handler: str
) -> None:
    await router._handle_new_session(_msg("/new"))
    handled = AsyncMock()
    monkeypatch.setattr(router, handler, handled)

    await router._dispatch_system_command(_msg("/command"), action, "")
    await asyncio.sleep(0)

    handled.assert_awaited_once()
    assert handled.await_args.args[0].metadata.get(_NEW) is True
