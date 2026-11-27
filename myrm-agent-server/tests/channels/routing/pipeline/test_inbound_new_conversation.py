"""The new conversation a flagged inbound message asks for is opened by the dispatch, once, before any step claims the message.

A hosted deployment answers ``/new`` itself and flags the next message it forwards. The flag is a request about the
conversation, not about the message: whatever the message turns out to be (a command, an approval, a turn), the
conversation opens first, the flag never travels further, and a message that is dropped as a duplicate opens nothing.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from unittest.mock import AsyncMock, patch

import pytest

from app.channels.types import METADATA_NEW_CONVERSATION_KEY, InboundMessage

from .inbound_dispatch_testkit import PIPELINE, command_dispatch, dispatch_router, gate_submissions, inbound_msg, run_dispatch

_FLAGGED: dict[str, object] = {METADATA_NEW_CONVERSATION_KEY: True}


@pytest.fixture
def opened() -> Iterator[AsyncMock]:
    with patch(f"{PIPELINE}.start_new_conversation", AsyncMock(return_value=True)) as start:
        yield start


def _records(order: list[str], step: str) -> Callable[..., bool]:
    """A stand-in handler that notes in ``order`` that ``step`` ran, and succeeds."""

    def record(*_: object) -> bool:
        order.append(step)
        return True

    return record


@pytest.mark.asyncio
async def test_a_flagged_turn_opens_the_conversation_and_reaches_the_gate_without_the_flag(opened: AsyncMock) -> None:
    router = dispatch_router()

    await run_dispatch(router, inbound_msg("good morning", metadata=_FLAGGED))

    [call] = opened.await_args_list
    assert call.args[0].content == "good morning"
    [submitted] = gate_submissions(router)
    assert METADATA_NEW_CONVERSATION_KEY not in submitted.metadata
    assert submitted.metadata["locale"], "the conversation opens after the locale is resolved, for the failure reply"


@pytest.mark.asyncio
async def test_a_flagged_command_opens_the_conversation_before_the_command_runs(opened: AsyncMock) -> None:
    router = dispatch_router()
    command = command_dispatch(router)
    order: list[str] = []
    opened.side_effect = _records(order, "opened")
    command.side_effect = _records(order, "command")

    await run_dispatch(router, inbound_msg("/undo", metadata=_FLAGGED))

    assert order == ["opened", "command"]
    opened.assert_awaited_once()
    [call] = command.await_args_list
    claimed: InboundMessage = call.args[0]
    assert METADATA_NEW_CONVERSATION_KEY not in claimed.metadata
    assert gate_submissions(router) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("metadata", [{}, {METADATA_NEW_CONVERSATION_KEY: False}], ids=["no-flag", "flag-off"])
async def test_a_message_that_asks_for_nothing_opens_nothing_and_never_carries_the_flag(
    opened: AsyncMock, metadata: dict[str, object]
) -> None:
    router = dispatch_router()

    await run_dispatch(router, inbound_msg("hello", metadata=metadata))

    opened.assert_not_awaited()
    [submitted] = gate_submissions(router)
    assert METADATA_NEW_CONVERSATION_KEY not in submitted.metadata


@pytest.mark.asyncio
async def test_a_conversation_that_could_not_be_opened_drops_the_message(opened: AsyncMock) -> None:
    opened.return_value = False
    router = dispatch_router()

    await run_dispatch(router, inbound_msg("good morning", metadata=_FLAGGED))
    await run_dispatch(router, inbound_msg("/undo", message_id="m2", metadata=_FLAGGED))

    assert opened.await_count == 2
    assert gate_submissions(router) == []
    command_dispatch(router).assert_not_awaited()


@pytest.mark.asyncio
async def test_a_replayed_message_opens_nothing_a_second_time(opened: AsyncMock) -> None:
    router = dispatch_router()

    await run_dispatch(router, inbound_msg("good morning", metadata=_FLAGGED))
    await run_dispatch(router, inbound_msg("good morning", metadata=_FLAGGED))

    opened.assert_awaited_once()
    assert len(gate_submissions(router)) == 1
