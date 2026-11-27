"""Who may take the approval a group turn really asked: the member whose message started that turn.

``test_approval_authority`` opens the request the way a finished turn leaves it. Here the turn is real: a member writes
in the group, the router starts the run on its ``SessionGate``, the scripted agent stops at an approval card, the router
registers the request from the message that started the turn, and the answers that follow decide whether the run
resumes. Runs on the real ``AgentRouter`` and ``SessionGate`` with a scripted agent.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncGenerator
from typing import Literal

import pytest

from app.channels.routing.router import AgentRouter
from app.channels.types import InboundMessage, ProgressUpdate, QuickReply, StreamingText
from tests.support.pending_approvals import inbound_message, router_with_agent, said_by

_KEY = "telegram:c1"
_TIMEOUT = 5

AnswerKind = Literal["typed", "reaction"]


class _Agent:
    """The first plain turn stops at a tool approval card, a resume finishes the run; the resume messages are kept."""

    def __init__(self) -> None:
        self.resumes: list[InboundMessage] = []
        self.asked = asyncio.Event()
        self.resumed = asyncio.Event()

    async def execute_stream(self, msg: InboundMessage, user_id: str, **kwargs: object) -> AsyncGenerator[object, None]:
        if msg.resume_value is not None:
            self.resumes.append(msg)
            yield StreamingText(text="mail sent")
            self.resumed.set()
            return
        yield ProgressUpdate(
            label="send_email needs approval",
            quick_replies=(QuickReply(label="Approve", text="/approve", required=True),),
        )
        self.asked.set()


def _in_group(sender: str, content: str, message_id: str) -> InboundMessage:
    """``content`` as ``sender`` writes it in the group chat ``c1``, addressed to the bot."""
    return dataclasses.replace(inbound_message(content, group=True), sender_id=sender, message_id=message_id, mentioned=True)


def _answer(kind: AnswerKind, sender: str, card: str, message_id: str) -> InboundMessage:
    """``sender`` approves once: by typing the command, or by reacting to the approval card ``card``."""
    if kind == "typed":
        return _in_group(sender, "/approve", message_id)
    reaction = _in_group(sender, "\U0001f44d", message_id)
    return dataclasses.replace(reaction, metadata={**reaction.metadata, "reaction": True, "target_message_id": card})


async def _route(router: AgentRouter, msg: InboundMessage) -> None:
    """Route ``msg`` and let the approval handler task that the claim step starts run to its end."""
    await router._dispatch_inbound_message(msg)
    handlers = [t for t in asyncio.all_tasks() if getattr(t.get_coro(), "__qualname__", "").endswith("_handle_approval_command")]
    await asyncio.gather(*handlers)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["typed", "reaction"])
async def test_in_a_group_only_the_member_whose_turn_asked_takes_the_approval(kind: AnswerKind) -> None:
    agent = _Agent()
    router, bus = router_with_agent(agent)

    await router._dispatch_inbound_message(_in_group("alice", "mail the weekly report", "m-alice-1"))
    await asyncio.wait_for(agent.asked.wait(), timeout=_TIMEOUT)
    card = bus.send_tracked.return_value
    told_before = said_by(bus)

    await _route(router, _answer(kind, "bob", card, "m-bob-1"))

    assert agent.resumes == []
    assert _KEY in router._pending_approvals
    assert said_by(bus) == told_before

    await _route(router, _answer(kind, "alice", card, "m-alice-2"))
    await asyncio.wait_for(agent.resumed.wait(), timeout=_TIMEOUT)

    (resume,) = agent.resumes
    assert resume.sender_id == "alice"
    assert _KEY not in router._pending_approvals
