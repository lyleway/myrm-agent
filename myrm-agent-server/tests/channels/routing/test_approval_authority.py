"""Who may take a chat's open approval request in a group: the member whose turn asked it, whichever way they answer.

Runs the real ``AgentRouter`` dispatch: the claim step reads what was written or reacted, the approval handler takes the
open request or leaves it. The session gate only records what it is asked to run, so ``submit`` shows both what
resumed a run and what would have become a chat turn. A DM is one person's chat with the bot, so whoever writes there
answers.
"""

from __future__ import annotations

import asyncio
import dataclasses
import itertools
from unittest.mock import MagicMock, patch

import pytest

from app.channels.routing.router import AgentRouter
from app.channels.types import InboundMessage
from tests.support.pending_approvals import asked_in, finished_turn, inbound_message, router_with_agent, said_by

_KEY = "telegram:c1"
_CARD = "card-1"
_message_ids = itertools.count(1)

# One answer of every kind a person can type: slash commands, bare words, a denial with a reason, a batch
_TYPED = ["/approve", "/approve-always", "/deny", "/deny not now", "1", "aa", "同意", "/batch a"]
_REACTIONS = ["\U0001f44d", "\u267e\ufe0f", "\U0001f44e"]


def _router(*, asked_by: str = "alice", group: bool = True) -> tuple[AgentRouter, MagicMock]:
    """A router whose chat ``c1`` waits on an approval that ``asked_by`` asked, as a finished turn leaves it."""
    router, bus = router_with_agent(record_resumes=True)
    asked = asked_in(sender_id=asked_by, user_id=asked_by, is_group=group, card_message_id=_CARD)
    router._pending_approvals.register(_KEY, asked, None)
    router._active_tasks[_KEY] = finished_turn(asked_by)
    return router, bus


def _from(sender: str, text: str, *, group: bool = True) -> InboundMessage:
    """``text`` as ``sender`` writes it in chat ``c1``."""
    message = inbound_message(text, group=group)
    return dataclasses.replace(message, sender_id=sender, message_id=f"m{next(_message_ids)}")


def _reaction_from(sender: str, emoji: str, *, group: bool = True) -> InboundMessage:
    """``emoji`` as ``sender`` reacts with it to the approval card."""
    message = _from(sender, emoji, group=group)
    return dataclasses.replace(message, metadata={**message.metadata, "reaction": True, "target_message_id": _CARD})


async def _dispatch(router: AgentRouter, msg: InboundMessage) -> None:
    """Route ``msg`` and let the approval handler task that the claim step starts run to its end."""
    await router._dispatch_inbound_message(msg)
    handlers = [t for t in asyncio.all_tasks() if getattr(t.get_coro(), "__qualname__", "").endswith("_handle_approval_command")]
    await asyncio.gather(*handlers)


def _assert_left_open(router: AgentRouter, bus: MagicMock) -> None:
    """Nothing resumed the run, nothing started a turn, the group was not told, and the card still invites an answer."""
    assert _KEY in router._pending_approvals
    router._gate.submit.assert_not_called()
    assert said_by(bus) == []
    bus.edit_channel_message.assert_not_awaited()


def _assert_taken(router: AgentRouter) -> None:
    assert _KEY not in router._pending_approvals
    router._gate.submit.assert_called_once()
    assert router._gate.submit.call_args.args[0].resume_value is not None


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", _TYPED)
async def test_a_group_bystander_typing_an_answer_takes_nothing_and_starts_no_turn(answer: str) -> None:
    router, bus = _router()

    await _dispatch(router, _from("bob", answer))

    _assert_left_open(router, bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", _TYPED)
async def test_the_member_who_asked_answers_by_typing(answer: str) -> None:
    router, _ = _router()

    await _dispatch(router, _from("alice", answer))

    _assert_taken(router)


@pytest.mark.asyncio
@pytest.mark.parametrize("emoji", _REACTIONS)
async def test_a_group_bystander_reacting_to_the_card_takes_nothing(emoji: str) -> None:
    router, bus = _router()

    await _dispatch(router, _reaction_from("bob", emoji))

    _assert_left_open(router, bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("emoji", _REACTIONS)
async def test_the_member_who_asked_answers_by_reacting(emoji: str) -> None:
    router, _ = _router()

    await _dispatch(router, _reaction_from("alice", emoji))

    _assert_taken(router)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["/approve", "1"])
async def test_a_dm_answer_needs_no_sender_check(answer: str) -> None:
    router, _ = _router(group=False)

    await _dispatch(router, _from("someone-else", answer, group=False))

    _assert_taken(router)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["/approve", "1"])
async def test_an_answer_flagged_as_a_dm_does_not_make_a_groups_request_a_dms(answer: str) -> None:
    """The request says it was asked in a group; a channel that cannot tell reports every answer of its own as a DM."""
    router, bus = _router()

    await _dispatch(router, _from("bob", answer, group=False))

    _assert_left_open(router, bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("emoji", _REACTIONS)
async def test_a_reaction_flagged_as_a_dm_does_not_make_a_groups_request_a_dms(emoji: str) -> None:
    router, bus = _router()

    await _dispatch(router, _reaction_from("bob", emoji, group=False))

    _assert_left_open(router, bus)


@pytest.mark.asyncio
async def test_a_group_flag_on_the_answer_counts_when_the_request_carries_none() -> None:
    """A request that a button click started is flagged as a DM (the click is); the typed answer still shows the group."""
    router, bus = _router(group=False)

    await _dispatch(router, _from("bob", "/approve", group=True))

    _assert_left_open(router, bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("answer", ["/approve", "1"])
async def test_a_group_answer_that_names_no_sender_takes_nothing(answer: str) -> None:
    router, bus = _router()

    await _dispatch(router, _from("", answer))

    _assert_left_open(router, bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("sender", ["alice", ""])
async def test_a_request_whose_asker_is_unknown_is_taken_by_nobody_in_a_group(sender: str) -> None:
    router, bus = _router(asked_by="")

    await _dispatch(router, _from(sender, "/approve"))

    _assert_left_open(router, bus)


@pytest.mark.asyncio
async def test_the_member_who_asked_decides_not_the_turn_that_happens_to_run_in_the_chat() -> None:
    """A delegated run asks while another member's turn holds the chat's registry entry: the request is the delegator's."""
    router, bus = _router(asked_by="alice")
    router._active_tasks[_KEY] = finished_turn("bob")

    await _dispatch(router, _from("bob", "/approve"))
    _assert_left_open(router, bus)

    await _dispatch(router, _from("alice", "/approve"))
    _assert_taken(router)


@pytest.mark.asyncio
async def test_the_asker_is_read_when_the_answer_is_taken_not_when_it_arrives() -> None:
    """Screening the reason of ``/deny <reason>`` is the one wait before the request is taken.

    If the chat's request changes hands during it, the answer is judged against the request it would take.
    """
    router, _ = _router(asked_by="alice")

    async def another_request_replaces_it(*_: object) -> bool:
        router._pending_approvals.register(
            _KEY, asked_in(sender_id="bob", user_id="bob", is_group=True, card_message_id="card-2"), None
        )
        return False

    with patch("app.channels.routing.commands.router_commands.approval.hold_risky_inbound", another_request_replaces_it):
        await _dispatch(router, _from("alice", "/deny not now"))

    taken = router._pending_approvals.get(_KEY)
    assert taken is not None
    assert taken.message.sender_id == "bob", "alice's answer did not take bob's request"
    router._gate.submit.assert_not_called()
