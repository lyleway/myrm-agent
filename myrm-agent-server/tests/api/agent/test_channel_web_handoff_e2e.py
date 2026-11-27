"""Real-chain E2E: an IM turn run by a live model carries a button that opens the chat it was stored in.

The chain is the one of ``tests.support.channel_handoff_stack``: the real router, bus, executor, model, chat store and
config rows, with only the IM platform edge recorded. This module is the main flow on a platform that sends each reply
as a message of its own; ``test_channel_web_handoff_paths_e2e`` runs the other ways a reply reaches the user.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from collections.abc import AsyncIterator

import httpx
import pytest
from httpx import ASGITransport

from app.channels.types import ChannelCapabilities
from app.services.chat.chat_service import ChatService
from tests.support.channel_handoff_stack import (
    PUBLIC_BASE,
    Stack,
    direct_message,
    handoff_chat_id,
    reply_containing,
    running_stack,
    seeded_config,
    stored_chats_removed,
    stored_texts,
)
from tests.support.minimal_app import build_minimal_app

logger = logging.getLogger(__name__)


@pytest.fixture
async def stack() -> AsyncIterator[Stack]:
    async with seeded_config(PUBLIC_BASE), running_stack(ChannelCapabilities(buttons=True)) as running:
        yield running


@pytest.fixture
async def _chats_removed() -> AsyncIterator[None]:
    async with stored_chats_removed():
        yield


@pytest.mark.e2e
@pytest.mark.asyncio
@pytest.mark.timeout(600)
@pytest.mark.skipif(not os.environ.get("BASIC_API_KEY"), reason="E2E test requires BASIC_API_KEY environment variable")
@pytest.mark.usefixtures("_chats_removed")
async def test_a_model_run_im_turn_carries_a_button_that_opens_the_chat_it_was_stored_in(stack: Stack) -> None:
    sender = f"peer-{uuid.uuid4().hex[:8]}"
    first, second, followup_marker = (uuid.uuid4().hex[:8] for _ in range(3))

    # Turn 1: the live model answers; the reply carries the button of the chat the turn was stored in.
    first_turn = direct_message(sender, f"Reply with exactly this single token and nothing else: HANDOFF-{first}")
    await stack.channel._dispatch_inbound(first_turn)
    first_reply = await reply_containing(stack, first_turn, f"HANDOFF-{first}")
    chat_id = handoff_chat_id(first_reply)

    chat = await ChatService.get_chat_by_id(chat_id)
    assert chat is not None, "the button opens a chat that is not stored"
    assert chat.channel_session_key, "the opened chat is not the IM session's chat"
    user_texts, assistant_texts = await stored_texts(chat_id)
    assert any(f"HANDOFF-{first}" in text for text in user_texts), user_texts
    assert any(f"HANDOFF-{first}" in text for text in assistant_texts), assistant_texts

    # Turn 2: the same sender continues the conversation, so the button opens the very same chat.
    follow_up_turn = direct_message(sender, f"Reply with exactly this single token and nothing else: HANDOFF-{second}")
    await stack.channel._dispatch_inbound(follow_up_turn)
    second_reply = await reply_containing(stack, follow_up_turn, f"HANDOFF-{second}")
    assert handoff_chat_id(second_reply) == chat_id
    user_texts, assistant_texts = await stored_texts(chat_id)
    logger.info(
        "chat %s (session %s) holds %d user and %d assistant messages",
        chat_id,
        chat.channel_session_key,
        len(user_texts),
        len(assistant_texts),
    )
    assert any(f"HANDOFF-{first}" in text for text in assistant_texts)
    assert any(f"HANDOFF-{second}" in text for text in assistant_texts)

    # A message said later in that conversation without a user turn (a wake or background result) links the same chat.
    assert await stack.router.deliver_followup(follow_up_turn, f"FOLLOWUP-{followup_marker}", stored_chat_id=chat_id)
    followup = await reply_containing(stack, follow_up_turn, f"FOLLOWUP-{followup_marker}", timeout_sec=30.0)
    assert handoff_chat_id(followup) == chat_id

    # The page behind the link reads this chat through the chats API: the conversation is there to render.
    async with httpx.AsyncClient(
        transport=ASGITransport(app=build_minimal_app(preset="chats")), base_url="http://testserver", timeout=60.0
    ) as client:
        landing = await client.get(f"/api/v1/chats/{chat_id}")
    assert landing.status_code == 200, landing.text
    body = json.dumps(landing.json(), ensure_ascii=False)
    assert f"HANDOFF-{first}" in body
    assert f"HANDOFF-{second}" in body
