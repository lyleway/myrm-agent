"""The pre-reply idle gate measures idleness against the last message before this turn, read from a real database."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from app.database.dto import ChatCreate
from app.database.dto.message import MessageCreate
from app.platform_utils import get_session_factory
from app.services.chat.chat_service import ChatService
from app.services.chat.compact_service import CompactResult
from app.services.chat.stale_compact_gate import maybe_compact_stale_chat_before_turn
from tests.support.minimal_app import build_minimal_app

build_minimal_app(preset="chats")

_IDLE_HOURS = 2
_THRESHOLD_SECONDS = 1800
_ESTIMATED_TOKENS = 50_000


async def _seed_chat_idle_for_hours(chat_id: str) -> None:
    """Twelve messages that were sent `_IDLE_HOURS` ago."""
    sent_at = datetime.now(UTC) - timedelta(hours=_IDLE_HOURS)
    messages = [
        MessageCreate(
            messageId=f"{chat_id}-m{index}",
            chatId=chat_id,
            role="user" if index % 2 == 0 else "assistant",
            content=f"Earlier message {index}",
            createdAt=sent_at + timedelta(seconds=index),
        )
        for index in range(12)
    ]
    await ChatService.create_or_update_chat(ChatCreate(chat_id=chat_id, title="Idle chat", messages=messages))


async def _run_gate(chat_id: str, *, turn_message_id: str | None) -> tuple[CompactResult, AsyncMock]:
    compact = AsyncMock(return_value=CompactResult(compacted=True, tokens_saved=900, message_count=12))
    with (
        patch(
            "app.services.chat.stale_compact_gate.estimate_idle_compact_request_tokens",
            AsyncMock(return_value=(_ESTIMATED_TOKENS, 12)),
        ),
        patch("app.services.chat.stale_compact_gate.compact_chat", compact),
    ):
        async with get_session_factory()() as db:
            result = await maybe_compact_stale_chat_before_turn(
                db,
                chat_id,
                idle_after_seconds=_THRESHOLD_SECONDS,
                max_context_tokens=128_000,
                turn_message_id=turn_message_id,
            )
    return result, compact


async def _commit_turn_message(chat_id: str) -> str:
    """The user row of the turn the gate runs for, committed before the gate as the Web turn does."""
    message_id = f"{chat_id}-turn"
    await ChatService.ensure_chat_and_append_user_message(
        chat_id=chat_id,
        content="What did we decide?",
        sent_at=datetime.now(UTC),
        sent_timezone="UTC",
        message_id=message_id,
    )
    return message_id


@pytest.mark.asyncio
async def test_the_message_of_this_turn_does_not_make_an_idle_chat_look_active() -> None:
    chat_id = f"idle-anchor-{uuid.uuid4().hex[:8]}"
    await _seed_chat_idle_for_hours(chat_id)
    turn_message_id = await _commit_turn_message(chat_id)

    result, compact = await _run_gate(chat_id, turn_message_id=turn_message_id)

    assert result.compacted is True, result.reason
    assert result.attempted is True
    compact.assert_awaited_once()


@pytest.mark.asyncio
async def test_a_chat_that_was_active_just_before_this_turn_is_not_compacted() -> None:
    chat_id = f"idle-anchor-{uuid.uuid4().hex[:8]}"
    await _seed_chat_idle_for_hours(chat_id)
    await ChatService.ensure_chat_and_append_user_message(
        chat_id=chat_id,
        content="Still here",
        sent_at=datetime.now(UTC) - timedelta(seconds=30),
        sent_timezone="UTC",
        message_id=f"{chat_id}-recent",
    )
    turn_message_id = await _commit_turn_message(chat_id)

    result, compact = await _run_gate(chat_id, turn_message_id=turn_message_id)

    assert result.compacted is False
    assert result.reason is not None
    assert result.reason.startswith("idle_below_threshold")
    compact.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_chat_whose_only_message_is_this_turn_has_nothing_to_measure() -> None:
    chat_id = f"idle-anchor-{uuid.uuid4().hex[:8]}"
    turn_message_id = await _commit_turn_message(chat_id)

    result, compact = await _run_gate(chat_id, turn_message_id=turn_message_id)

    assert result.compacted is False
    assert result.reason == "no_messages"
    compact.assert_not_awaited()
