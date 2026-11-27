"""Real-chain E2E: every way the web hand-off of an IM reply reaches the user, and where it must be absent.

The chain is the one of ``tests.support.channel_handoff_stack``: the real router, executor, live model, chat store and
config rows, with only the IM platform edge recorded. ``test_channel_web_handoff_e2e`` is the main flow on a platform that
sends each reply as a message with buttons; this module runs the rest: the three shapes a reply takes on its way to the
platform, a turn that fails after its chat was stored (the model refuses it, the executor crashes), the notices that end
a turn before the agent works (a spent group budget, an instance never set up with a model), an instance without a public
address, two senders in two languages at once, two members of one group, the reply of a wake turn, and a new conversation.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from dataclasses import dataclass
from unittest import mock

import pytest

from app.channels.i18n import channel_t
from app.channels.routing.friendly_errors import ERROR_TYPE_METADATA_KEY
from app.channels.types import ChannelCapabilities, ReactionLevel
from app.core.channel_bridge.agent_executor.session import build_channel_budget_key
from app.core.channel_bridge.wake_return import ChannelWakeReplyReturner, get_wake_origins
from app.services.budget.channel_budget import get_channel_budget_registry
from app.services.budget.channel_budget_policy import ChannelBudgetPolicy
from app.services.chat.chat_service import ChatService
from tests.support.channel_handoff_stack import (
    PUBLIC_BASE,
    direct_message,
    handoff_buttons,
    handoff_chat_id,
    linked_chat_id,
    reply_containing,
    running_stack,
    seeded_config,
    stored_chat_ids,
    stored_chats_removed,
    stored_texts,
)
from tests.support.e2e_provider_seed import NONEXISTENT_MODEL_ID

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.asyncio,
    pytest.mark.timeout(600),
    pytest.mark.skipif(not os.environ.get("BASIC_API_KEY"), reason="E2E test requires BASIC_API_KEY environment variable"),
]


def _ask(token: str) -> str:
    return f"Reply with exactly this single token and nothing else: {token}"


@dataclass(frozen=True)
class _Shape:
    """How a platform takes a reply: what it can do, and what the turn then does with the reply."""

    capabilities: ChannelCapabilities
    reaction_level: ReactionLevel
    via_edit: bool  # the reply replaces the placeholder shown while the turn ran
    native_button: bool  # the link is a button; otherwise it is a line of text under the reply


@pytest.mark.parametrize(
    "shape",
    [
        pytest.param(
            _Shape(ChannelCapabilities(buttons=True, edit=True), ReactionLevel.SIMPLE, via_edit=True, native_button=True),
            id="placeholder-edit-with-button",
        ),
        pytest.param(
            _Shape(ChannelCapabilities(edit=True), ReactionLevel.SIMPLE, via_edit=True, native_button=False),
            id="placeholder-edit-with-link-text",
        ),
        pytest.param(
            _Shape(ChannelCapabilities(), ReactionLevel.OFF, via_edit=False, native_button=False),
            id="message-with-link-text",
        ),
    ],
)
async def test_every_way_a_reply_reaches_the_platform_links_the_chat_it_was_stored_in(shape: _Shape) -> None:
    token = f"SHAPE-{uuid.uuid4().hex[:8]}"
    async with (
        stored_chats_removed(),
        seeded_config(PUBLIC_BASE),
        running_stack(shape.capabilities, reaction_level=shape.reaction_level) as stack,
    ):
        origin = direct_message(f"peer-{uuid.uuid4().hex[:8]}", _ask(token))
        await stack.channel._dispatch_inbound(origin)
        reply = await reply_containing(stack, origin, token)

        # A platform that edits shows a placeholder first and the reply replaces it; any other gets a message.
        assert bool(stack.channel.placeholders) is shape.via_edit
        if shape.via_edit:
            assert any(reply is edit for edit in stack.channel.edits), "the reply did not replace the placeholder"
            assert not any(token in message.content for message in stack.channel.sent), "the reply was also sent as a message"
        else:
            assert not stack.channel.edits

        # The link is a button where the platform has buttons, else a line of text.
        if shape.native_button:
            chat_id = handoff_chat_id(reply)
        else:
            assert not reply.components, reply.components
            chat_id = linked_chat_id(reply.content)

        assert await ChatService.get_chat_by_id(chat_id) is not None, "the link opens a chat that is not stored"
        user_texts, assistant_texts = await stored_texts(chat_id)
        assert any(token in text for text in user_texts), user_texts
        assert any(token in text for text in assistant_texts), assistant_texts


async def test_a_turn_that_fails_after_its_chat_was_stored_still_links_that_chat() -> None:
    token = f"FAILED-{uuid.uuid4().hex[:8]}"
    async with (
        stored_chats_removed(),
        seeded_config(PUBLIC_BASE, model_id=NONEXISTENT_MODEL_ID),
        running_stack(ChannelCapabilities(buttons=True)) as stack,
    ):
        origin = direct_message(f"peer-{uuid.uuid4().hex[:8]}", _ask(token))
        await stack.channel._dispatch_inbound(origin)
        reply = await reply_containing(stack, origin, "")

        # The reply is the turn's error, not an answer, and still names the chat the question was stored in.
        assert ERROR_TYPE_METADATA_KEY in reply.metadata, reply.metadata
        assert token not in reply.content
        chat_id = handoff_chat_id(reply)
        user_texts, assistant_texts = await stored_texts(chat_id)
        assert any(token in text for text in user_texts), user_texts
        assert not any(token in text for text in assistant_texts), assistant_texts


async def test_a_turn_that_crashes_after_its_chat_was_stored_still_links_that_chat() -> None:
    token = f"CRASH-{uuid.uuid4().hex[:8]}"
    async with stored_chats_removed(), seeded_config(PUBLIC_BASE), running_stack(ChannelCapabilities(buttons=True)) as stack:
        origin = direct_message(f"peer-{uuid.uuid4().hex[:8]}", _ask(token))
        # The executor builds the runtime context after it stored the question, so this is a crash of the turn itself.
        with mock.patch(
            "app.services.agent.runtime_context.build_agent_runtime_context", side_effect=RuntimeError("injected crash")
        ):
            await stack.channel._dispatch_inbound(origin)
            reply = await reply_containing(stack, origin, "")

        # The member gets the safe wording of a failed turn, never the raw error, and the chat the question was stored in.
        assert ERROR_TYPE_METADATA_KEY in reply.metadata, reply.metadata
        assert "injected crash" not in reply.content
        user_texts, assistant_texts = await stored_texts(handoff_chat_id(reply))
        assert any(token in text for text in user_texts), user_texts
        assert not any(token in text for text in assistant_texts), assistant_texts


async def test_a_notice_issued_before_the_agent_starts_working_has_no_chat_to_link() -> None:
    suffix = uuid.uuid4().hex[:8]
    group = f"group-{suffix}"
    registry = get_channel_budget_registry()
    async with (
        stored_chats_removed(),
        seeded_config(PUBLIC_BASE),
        running_stack(ChannelCapabilities(buttons=True), groups=frozenset({group})) as stack,
    ):
        origin = direct_message(f"peer-{suffix}", _ask(f"BUDGET-{suffix}"), is_group=True, chat=group)
        budget_key = build_channel_budget_key(origin)
        # The group's daily budget is already spent, so the turn ends with a notice before any chat is stored.
        registry.configure(ChannelBudgetPolicy(channel_key=budget_key, daily_limit_usd=0.01), initial_cost=1.0)
        try:
            await stack.channel._dispatch_inbound(origin)
            notice = await reply_containing(stack, origin, "")
        finally:
            registry.remove(budget_key)

        assert notice.metadata.get(ERROR_TYPE_METADATA_KEY) == "channel_budget_blocked", notice.metadata
        assert channel_t("en", "channel_budget_blocked") in notice.content, notice.content
        assert not notice.components, notice.components
        assert "://" not in notice.content, notice.content
        assert await stored_chat_ids() == [], "the notice was issued before the turn stored a chat"


async def test_the_setup_notice_of_an_instance_without_a_model_carries_no_hand_off() -> None:
    token = f"FIRST-{uuid.uuid4().hex[:8]}"
    async with (
        stored_chats_removed(),
        seeded_config(PUBLIC_BASE, with_model=False),
        running_stack(ChannelCapabilities(buttons=True)) as stack,
    ):
        origin = direct_message(f"peer-{uuid.uuid4().hex[:8]}", _ask(token))
        await stack.channel._dispatch_inbound(origin)
        notice = await reply_containing(stack, origin, "")

        # The agent was never built, so what the member reads is the setup notice, with no button and no link.
        assert notice.metadata.get(ERROR_TYPE_METADATA_KEY) == "provider_not_configured", notice.metadata
        assert not notice.components, notice.components
        assert "://" not in notice.content, notice.content


async def test_an_instance_without_a_public_address_answers_without_a_hand_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CP_PUBLIC_INGRESS_URL", raising=False)
    token = f"NOADDR-{uuid.uuid4().hex[:8]}"
    async with stored_chats_removed(), seeded_config(None), running_stack(ChannelCapabilities(buttons=True)) as stack:
        origin = direct_message(f"peer-{uuid.uuid4().hex[:8]}", _ask(token))
        await stack.channel._dispatch_inbound(origin)
        reply = await reply_containing(stack, origin, token)

        # No address another device could open: the answer goes out as it is, with no link in any form.
        assert not reply.components, reply.components
        assert "://" not in reply.content, reply.content
        (chat_id,) = await stored_chat_ids()
        user_texts, assistant_texts = await stored_texts(chat_id)
        assert any(token in text for text in user_texts), user_texts
        assert any(token in text for text in assistant_texts), assistant_texts


async def test_two_senders_each_get_a_hand_off_to_their_own_chat_in_their_own_language() -> None:
    suffix = uuid.uuid4().hex[:8]
    english, chinese = f"EN-{suffix}", f"ZH-{suffix}"
    async with stored_chats_removed(), seeded_config(PUBLIC_BASE), running_stack(ChannelCapabilities(buttons=True)) as stack:
        english_turn = direct_message(f"peer-en-{suffix}", _ask(english))
        chinese_turn = direct_message(f"peer-zh-{suffix}", _ask(chinese), locale="zh-CN")
        await stack.channel._dispatch_inbound(english_turn)
        await stack.channel._dispatch_inbound(chinese_turn)
        english_reply, chinese_reply = await asyncio.gather(
            reply_containing(stack, english_turn, english), reply_containing(stack, chinese_turn, chinese)
        )

        (english_button,) = handoff_buttons(english_reply)
        (chinese_button,) = handoff_buttons(chinese_reply)
        assert english_button.label == channel_t("en", "web_continue_chat")
        assert chinese_button.label == channel_t("zh-CN", "web_continue_chat")
        assert english_button.label != chinese_button.label

        english_chat, chinese_chat = handoff_chat_id(english_reply), handoff_chat_id(chinese_reply)
        assert english_chat != chinese_chat
        english_texts = [text for texts in await stored_texts(english_chat) for text in texts]
        chinese_texts = [text for texts in await stored_texts(chinese_chat) for text in texts]
        assert any(english in text for text in english_texts), english_texts
        assert any(chinese in text for text in chinese_texts), chinese_texts
        assert not any(chinese in text for text in english_texts), english_texts
        assert not any(english in text for text in chinese_texts), chinese_texts


async def test_members_of_a_group_are_handed_off_to_the_chat_the_group_conversation_is_stored_in() -> None:
    suffix = uuid.uuid4().hex[:8]
    group = f"group-{suffix}"
    first, second = f"GA-{suffix}", f"GB-{suffix}"
    async with (
        stored_chats_removed(),
        seeded_config(PUBLIC_BASE),
        running_stack(ChannelCapabilities(buttons=True), groups=frozenset({group})) as stack,
    ):
        member_a = direct_message(f"peer-a-{suffix}", _ask(first), is_group=True, chat=group)
        await stack.channel._dispatch_inbound(member_a)
        chat_id = handoff_chat_id(await reply_containing(stack, member_a, first))

        said = len(stack.channel.said)
        member_b = direct_message(f"peer-b-{suffix}", _ask(second), is_group=True, chat=group)
        await stack.channel._dispatch_inbound(member_b)
        second_reply = await reply_containing(stack, member_b, second, start=said)

        # The group is one conversation: the second member's button opens the chat the first member's did.
        assert handoff_chat_id(second_reply) == chat_id
        assert await stored_chat_ids() == [chat_id]
        user_texts, assistant_texts = await stored_texts(chat_id)
        for token in (first, second):
            assert any(token in text for text in user_texts), user_texts
            assert any(token in text for text in assistant_texts), assistant_texts


async def test_a_wake_turn_reply_returned_to_the_chat_links_the_chat_the_conversation_is_stored_in() -> None:
    token, report = f"ASK-{uuid.uuid4().hex[:8]}", f"REPORT-{uuid.uuid4().hex[:8]}"
    async with stored_chats_removed(), seeded_config(PUBLIC_BASE), running_stack(ChannelCapabilities(buttons=True)) as stack:
        origin = direct_message(f"peer-{uuid.uuid4().hex[:8]}", _ask(token))
        await stack.channel._dispatch_inbound(origin)
        chat_id = handoff_chat_id(await reply_containing(stack, origin, token))

        # The turn left the owner's chat in the origins memory; the wake turn of that chat reports back by its chat id.
        said = len(stack.channel.said)
        gateway = mock.MagicMock(deliver_followup=stack.router.deliver_followup)
        await ChannelWakeReplyReturner(gateway, get_wake_origins()).return_reply(chat_id, f"Background result: {report}")
        returned = await reply_containing(stack, origin, report, start=said, timeout_sec=30.0)

        assert handoff_chat_id(returned) == chat_id


async def test_a_new_conversation_gets_its_own_chat_and_a_hand_off_to_it() -> None:
    suffix = uuid.uuid4().hex[:8]
    before, after = f"BEFORE-{suffix}", f"AFTER-{suffix}"
    async with stored_chats_removed(), seeded_config(PUBLIC_BASE), running_stack(ChannelCapabilities(buttons=True)) as stack:
        sender = f"peer-{suffix}"
        first_turn = direct_message(sender, _ask(before))
        await stack.channel._dispatch_inbound(first_turn)
        old_chat = handoff_chat_id(await reply_containing(stack, first_turn, before))

        said = len(stack.channel.said)
        reset = direct_message(sender, "/new")
        await stack.channel._dispatch_inbound(reset)
        confirmation = await reply_containing(stack, reset, "", start=said)
        assert not handoff_buttons(confirmation), "the answer to a command is not a turn of a chat"

        second_turn = direct_message(sender, _ask(after))
        await stack.channel._dispatch_inbound(second_turn)
        new_chat = handoff_chat_id(await reply_containing(stack, second_turn, after))

        assert new_chat != old_chat
        old_texts = [text for texts in await stored_texts(old_chat) for text in texts]
        new_texts = [text for texts in await stored_texts(new_chat) for text in texts]
        assert any(before in text for text in old_texts), old_texts
        assert not any(after in text for text in old_texts), old_texts
        assert any(after in text for text in new_texts), new_texts
        assert not any(before in text for text in new_texts), new_texts
