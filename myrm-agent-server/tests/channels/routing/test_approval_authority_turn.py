"""Who may take the approval a group turn really asked: the member whose message started that turn.

``test_approval_authority`` opens the request the way a finished turn leaves it. Here the request comes from a real run:
a member writes in the group (or delegates the work to a background run), the router starts the run, the scripted agent
stops at an approval card, the router registers the request from the message that started the run, and the answers that
follow decide whether the run resumes. A reaction is the one Telegram's own parser builds, so it carries what a platform
delivers: the id of the card as its message id. Runs on the real ``AgentRouter`` and ``SessionGate`` with a scripted agent.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import AsyncGenerator, Callable
from typing import Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.channels.routing.router import AgentRouter
from app.channels.types import InboundMessage, ProgressUpdate, QuickReply, StreamingText
from tests.channels.providers.telegram.telegram_channel_factories import make_channel
from tests.support.pending_approvals import inbound_message, router_with_agent, said_by
from tests.support.risk_gate import build_service_with_rules

_CHAT = "-1001234567"
_KEY = f"telegram:{_CHAT}"
_CARD = 5
_ALICE, _BOB, _CAROL = "101", "202", "303"
_THUMBS_UP, _FIRE = "\U0001f44d", "\U0001f525"
_TIMEOUT = 5

AnswerKind = Literal["typed", "reaction"]


class _Agent:
    """The first plain turn stops at a tool approval card, a resume finishes the run; the resume messages are kept."""

    def __init__(self) -> None:
        self.resumes: list[InboundMessage] = []
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


def _in_group(sender: str, content: str, message_id: str) -> InboundMessage:
    """``content`` as ``sender`` writes it in the group, addressed to the bot."""
    return dataclasses.replace(
        inbound_message(content, group=True),
        sender_id=sender,
        user_id=sender,
        chat_id=_CHAT,
        message_id=message_id,
        mentioned=True,
    )


def _reaction(sender: str, emoji: str = _THUMBS_UP) -> InboundMessage:
    """The reaction of ``sender`` on the approval card as Telegram's parser builds it, forwarded by the control plane."""
    parsed = make_channel()._parse_update(
        {
            "update_id": 1,
            "message_reaction": {
                "chat": {"id": int(_CHAT), "type": "supergroup"},
                "message_id": _CARD,
                "user": {"id": int(sender)},
                "date": 1700000000,
                "new_reaction": [{"type": "emoji", "emoji": emoji}],
            },
        }
    )
    assert parsed is not None
    return dataclasses.replace(parsed, user_id=sender, metadata={**parsed.metadata, **inbound_message().metadata})


def _answer(kind: AnswerKind, sender: str, message_id: str) -> InboundMessage:
    """``sender`` approves once: by typing the command, or by reacting to the approval card."""
    return _in_group(sender, "/approve", message_id) if kind == "typed" else _reaction(sender)


async def _route(router: AgentRouter, *messages: InboundMessage) -> None:
    """Route the messages one after the other without letting a handler run in between, then let the handlers finish."""
    for msg in messages:
        await router._dispatch_inbound_message(msg)
    handlers = [t for t in asyncio.all_tasks() if getattr(t.get_coro(), "__qualname__", "").endswith("_handle_approval_command")]
    await asyncio.gather(*handlers)


async def _until(condition: Callable[[], bool]) -> None:
    async with asyncio.timeout(_TIMEOUT):
        while not condition():
            await asyncio.sleep(0)


async def _asked_by(
    sender: str, content: str, agent: _Agent, *, card_id: str | None = str(_CARD)
) -> tuple[AgentRouter, MagicMock]:
    """A router whose group waits on the approval that the run started by ``content`` stops at."""
    router, bus = router_with_agent(agent)
    bus.send_tracked.return_value = card_id
    with patch("app.services.risk.detection.get_detection_service", return_value=build_service_with_rules([])):
        await router._dispatch_inbound_message(_in_group(sender, content, f"m-{sender}-1"))
    await _until(lambda: _KEY in router._pending_approvals)
    return router, bus


async def _taken_by_the_member_who_asked(agent: _Agent, router: AgentRouter, kind: AnswerKind, bus: MagicMock) -> None:
    """A member who did not ask answers and changes nothing, then the member who asked answers and takes the request."""
    told_before = said_by(bus)

    await _route(router, _answer(kind, _BOB, "m-bob-1"))

    assert agent.resumes == []
    assert _KEY in router._pending_approvals
    assert said_by(bus) == told_before

    await _route(router, _answer(kind, _ALICE, "m-alice-2"))
    await asyncio.wait_for(agent.resumed.wait(), timeout=_TIMEOUT)

    (resume,) = agent.resumes
    assert resume.sender_id == _ALICE
    assert _KEY not in router._pending_approvals


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["typed", "reaction"])
async def test_in_a_group_only_the_member_whose_turn_asked_takes_the_approval(kind: AnswerKind) -> None:
    agent = _Agent()
    router, bus = await _asked_by(_ALICE, "mail the weekly report", agent)

    await _taken_by_the_member_who_asked(agent, router, kind, bus)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["typed", "reaction"])
async def test_the_approval_of_a_delegated_run_is_taken_only_by_the_member_who_delegated_it(
    kind: AnswerKind, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(AgentRouter, "_check_admin_permission", AsyncMock(return_value=True))
    agent = _Agent()
    router, bus = await _asked_by(_ALICE, "/delegate mail the weekly report", agent)

    await _taken_by_the_member_who_asked(agent, router, kind, bus)


@pytest.mark.asyncio
async def test_a_reaction_that_answers_nothing_does_not_use_up_the_askers_next_reaction() -> None:
    agent = _Agent()
    router, _ = await _asked_by(_ALICE, "mail the weekly report", agent)

    await _route(router, _reaction(_ALICE, _FIRE))
    assert agent.resumes == []
    assert _KEY in router._pending_approvals

    await _route(router, _reaction(_ALICE))
    await asyncio.wait_for(agent.resumed.wait(), timeout=_TIMEOUT)

    assert _KEY not in router._pending_approvals


@pytest.mark.asyncio
@pytest.mark.parametrize("asker_first", [True, False], ids=["asker-first", "asker-last"])
async def test_reactions_that_arrive_together_leave_one_taker_and_no_stray_reply(asker_first: bool) -> None:
    agent = _Agent()
    router, bus = await _asked_by(_ALICE, "mail the weekly report", agent)
    told_before = said_by(bus)
    reactions = [_reaction(_ALICE), _reaction(_BOB), _reaction(_CAROL)]
    if not asker_first:
        reactions.reverse()

    await _route(router, *reactions)
    await asyncio.wait_for(agent.resumed.wait(), timeout=_TIMEOUT)

    (resume,) = agent.resumes
    assert resume.sender_id == _ALICE
    assert said_by(bus) == told_before


@pytest.mark.asyncio
async def test_an_approval_card_the_channel_gave_no_id_is_taken_like_any_other() -> None:
    agent = _Agent()
    router, bus = await _asked_by(_ALICE, "mail the weekly report", agent, card_id=None)

    await _taken_by_the_member_who_asked(agent, router, "typed", bus)

    bus.edit_channel_message.assert_not_awaited()
