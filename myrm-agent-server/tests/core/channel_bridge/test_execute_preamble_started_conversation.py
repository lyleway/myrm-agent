"""A conversation the person opened on request is a deliberate blank start: nothing is backfilled and no reset is announced.

A key with a reset epoch and no history is a cold start the executor fills with the platform's recent messages and, when the
person asked for it, announces as a reset. The conversation of ``/new`` is empty on purpose, and the messages it would be
filled with belong to the conversation the person chose to leave.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.channels.types import InboundMessage, ProgressUpdate
from app.core.channel_bridge.agent_executor.execute_preamble.session import ChannelSessionContext, resolve_channel_session_context

_SESSION = "app.core.channel_bridge.agent_executor.execute_preamble.session"
_EPOCH_KEY = "telegram:dm:user-1:e=2026-01-01"
_STARTED_KEY = "telegram:dm:user-1:e=s20260101T120000"
_PEOPLE = {
    "daily": {"sessionPolicy": {"mode": "daily", "dailyResetHour": 4, "notifyOnReset": True}},
    "idle": {"sessionPolicy": {"mode": "idle", "idleMinutes": 120, "notifyOnReset": True}},
}
_BOTH_POLICIES = pytest.mark.parametrize("person", _PEOPLE.values(), ids=_PEOPLE.keys())


async def _preamble_of_an_empty_chat(session_key: str, person: dict[str, object]) -> tuple[ChannelSessionContext, AsyncMock]:
    """The preamble of a first message that resolves to ``session_key``, whose chat holds no history; and the backfill."""
    backfill = AsyncMock()
    msg = InboundMessage(channel="telegram", sender_id="user-1", content="hi", metadata={"locale": "en"})
    with (
        patch(f"{_SESSION}.resolve_session_key", new=AsyncMock(return_value=session_key)),
        patch(f"{_SESSION}._detect_cold_start", new=AsyncMock(return_value=True)),
        patch(f"{_SESSION}.maybe_backfill_channel_history", new=backfill),
        patch(f"{_SESSION}.persist_and_load_history", new=AsyncMock(return_value=("chat-1", []))),
    ):
        context = await resolve_channel_session_context(
            MagicMock(),
            msg,
            query="hi",
            is_resume=False,
            topic_context=None,
            resolved_agent_id=None,
            resolved_profile=None,
            personal_settings_dict=person,
        )
    return context, backfill


@pytest.mark.asyncio
@_BOTH_POLICIES
async def test_an_epoch_key_without_history_is_a_cold_start_that_is_backfilled_and_announced(person: dict[str, object]) -> None:
    context, backfill = await _preamble_of_an_empty_chat(_EPOCH_KEY, person)

    [backfill_call] = backfill.await_args_list
    assert backfill_call.kwargs["is_cold_start"] is True
    assert context.session_was_auto_reset is True
    assert [type(event) for event in context.pre_events] == [ProgressUpdate]
    assert isinstance(context.query, str) and context.query.startswith("[System note") and context.query.endswith("hi")


@pytest.mark.asyncio
@_BOTH_POLICIES
async def test_a_conversation_opened_on_request_is_neither_backfilled_nor_announced(person: dict[str, object]) -> None:
    context, backfill = await _preamble_of_an_empty_chat(_STARTED_KEY, person)

    [backfill_call] = backfill.await_args_list
    assert backfill_call.kwargs["is_cold_start"] is False
    assert context.session_was_auto_reset is False
    assert context.pre_events == ()
    assert context.query == "hi"
