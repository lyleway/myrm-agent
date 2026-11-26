"""The ``/new`` and ``/topic`` command handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.channels.i18n import get_text
from app.channels.routing.commands import (
    TopicCommand,
    handle_new_session,
    handle_topic_command,
)
from app.channels.types import OutboundMessage
from tests.channels.command_testkit import make_command_bus, make_command_msg


class TestHandleNewSession:
    @pytest.mark.asyncio
    async def test_opens_the_conversation_then_confirms(self) -> None:
        msg = make_command_msg(channel="tg", sender_id="u1")
        bus = make_command_bus()
        order: list[str] = []
        opened = AsyncMock(side_effect=lambda _: order.append("opened"))
        bus.publish_outbound.side_effect = lambda _: order.append("confirmed")

        with patch("app.core.channel_bridge.command_session.start_conversation", opened):
            await handle_new_session(msg, bus)

        assert order == ["opened", "confirmed"]
        opened.assert_awaited_once_with(msg)
        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert reply.content == get_text(msg, "new_session_started")

    @pytest.mark.asyncio
    async def test_uses_chat_id_when_present(self) -> None:
        msg = make_command_msg(channel="tg", sender_id="u1", chat_id="group1")
        bus = make_command_bus()

        with patch("app.core.channel_bridge.command_session.start_conversation", AsyncMock()):
            await handle_new_session(msg, bus)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert reply.recipient_id == "group1"

    @pytest.mark.asyncio
    async def test_a_conversation_that_could_not_be_opened_is_told_and_not_confirmed(self) -> None:
        msg = make_command_msg(channel="tg", sender_id="u1")
        bus = make_command_bus()

        with patch("app.core.channel_bridge.command_session.start_conversation", AsyncMock(side_effect=RuntimeError("db down"))):
            await handle_new_session(msg, bus)

        bus.publish_outbound.assert_called_once()
        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert reply.content == get_text(msg, "new_session_failed")
        assert reply.content != get_text(msg, "new_session_started")


class TestHandleTopicCommand:
    @pytest.mark.asyncio
    async def test_no_topic_resolver_no_thread(self) -> None:
        msg = make_command_msg(thread_id=None)
        cmd = TopicCommand(action="bind")
        bus = make_command_bus()

        await handle_topic_command(msg, cmd, bus, topic_resolver=None)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "not configured" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_no_topic_resolver(self) -> None:
        msg = make_command_msg(thread_id="t1")
        cmd = TopicCommand(action="bind")
        bus = make_command_bus()

        await handle_topic_command(msg, cmd, bus, topic_resolver=None)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "not configured" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_bind_success(self) -> None:
        msg = make_command_msg(thread_id="t1", chat_id="c1")
        cmd = TopicCommand(action="bind", agent_id="my-agent")
        bus = make_command_bus()

        topic_resolver = MagicMock()
        ctx = MagicMock()
        ctx.agent_id = "my-agent"
        topic_resolver.bind_topic = AsyncMock(return_value=ctx)

        channel_obj = MagicMock()
        channel_obj.send_placeholder = AsyncMock(return_value="msg123")
        channel_obj.pin_message = AsyncMock()
        bus.get_channel = MagicMock(return_value=channel_obj)

        await handle_topic_command(msg, cmd, bus, topic_resolver=topic_resolver)

        topic_resolver.bind_topic.assert_called_once()
        channel_obj.pin_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_bind_no_pin_msg(self) -> None:
        msg = make_command_msg(thread_id="t1", chat_id="c1")
        cmd = TopicCommand(action="bind")
        bus = make_command_bus()

        topic_resolver = MagicMock()
        ctx = MagicMock()
        ctx.agent_id = None
        topic_resolver.bind_topic = AsyncMock(return_value=ctx)

        channel_obj = MagicMock()
        channel_obj.send_placeholder = AsyncMock(return_value=None)
        bus.get_channel = MagicMock(return_value=channel_obj)

        await handle_topic_command(msg, cmd, bus, topic_resolver=topic_resolver)

        bus.publish_outbound.assert_called_once()

    @pytest.mark.asyncio
    async def test_unbind_success(self) -> None:
        msg = make_command_msg(thread_id="t1", chat_id="c1")
        cmd = TopicCommand(action="unbind")
        bus = make_command_bus()

        topic_resolver = MagicMock()
        topic_resolver.unbind_topic = AsyncMock(return_value=True)

        await handle_topic_command(msg, cmd, bus, topic_resolver=topic_resolver)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "unbound" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_unbind_not_found(self) -> None:
        msg = make_command_msg(thread_id="t1", chat_id="c1")
        cmd = TopicCommand(action="unbind")
        bus = make_command_bus()

        topic_resolver = MagicMock()
        topic_resolver.unbind_topic = AsyncMock(return_value=False)

        await handle_topic_command(msg, cmd, bus, topic_resolver=topic_resolver)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "no binding" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_topic_status_bound(self) -> None:
        msg = make_command_msg(thread_id="t1", chat_id="c1")
        cmd = TopicCommand(action="topic")
        bus = make_command_bus()

        topic_resolver = MagicMock()
        topic_ctx = MagicMock()
        topic_ctx.agent_id = "agent1"
        topic_ctx.bound_at = "2025-01-01"
        topic_ctx.enabled = True
        topic_resolver.resolve_topic = AsyncMock(return_value=topic_ctx)

        await handle_topic_command(msg, cmd, bus, topic_resolver=topic_resolver)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "agent1" in reply.content

    @pytest.mark.asyncio
    async def test_topic_status_no_binding(self) -> None:
        msg = make_command_msg(thread_id="t1", chat_id="c1")
        cmd = TopicCommand(action="topic")
        bus = make_command_bus()

        topic_resolver = MagicMock()
        topic_resolver.resolve_topic = AsyncMock(return_value=None)

        await handle_topic_command(msg, cmd, bus, topic_resolver=topic_resolver)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "defaults" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_topic_exception(self) -> None:
        msg = make_command_msg(thread_id="t1", chat_id="c1")
        cmd = TopicCommand(action="bind")
        bus = make_command_bus()

        topic_resolver = MagicMock()
        topic_resolver.bind_topic = AsyncMock(side_effect=RuntimeError("db error"))

        await handle_topic_command(msg, cmd, bus, topic_resolver=topic_resolver)

        bus.publish_outbound.assert_called_once()

    @pytest.mark.asyncio
    async def test_channel_level_bind(self) -> None:
        """Channel-level bind when thread_id is None."""
        msg = make_command_msg(thread_id=None, chat_id="c1")
        cmd = TopicCommand(action="bind", agent_id="support-agent")
        bus = make_command_bus()

        topic_resolver = MagicMock()
        ctx = MagicMock()
        ctx.agent_id = "support-agent"
        topic_resolver.bind_topic = AsyncMock(return_value=ctx)

        channel_obj = MagicMock()
        channel_obj.send_placeholder = AsyncMock(return_value=None)
        bus.get_channel = MagicMock(return_value=channel_obj)

        await handle_topic_command(msg, cmd, bus, topic_resolver=topic_resolver)

        topic_resolver.bind_topic.assert_called_once_with(channel="test", chat_id="c1", thread_id=None, agent_id="support-agent")
        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "channel" in reply.content.lower()
        assert "support-agent" in reply.content

    @pytest.mark.asyncio
    async def test_bind_rejects_search_agent(self) -> None:
        """IM /bind must surface the same Search-agent rejection as Settings API."""
        from app.core.channel_bridge.topic_config import SEARCH_AGENT_CHANNEL_BIND_MSG

        msg = make_command_msg(thread_id=None, chat_id="c1")
        cmd = TopicCommand(action="bind", agent_id="builtin-fast-search")
        bus = make_command_bus()

        topic_resolver = MagicMock()
        topic_resolver.bind_topic = AsyncMock(side_effect=ValueError(SEARCH_AGENT_CHANNEL_BIND_MSG))

        await handle_topic_command(msg, cmd, bus, topic_resolver=topic_resolver)

        bus.publish_outbound.assert_called_once()
        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "Search agents cannot" in reply.content

    @pytest.mark.asyncio
    async def test_channel_level_unbind(self) -> None:
        """Channel-level unbind when thread_id is None."""
        msg = make_command_msg(thread_id=None, chat_id="c1")
        cmd = TopicCommand(action="unbind")
        bus = make_command_bus()

        topic_resolver = MagicMock()
        topic_resolver.unbind_topic = AsyncMock(return_value=True)

        await handle_topic_command(msg, cmd, bus, topic_resolver=topic_resolver)

        topic_resolver.unbind_topic.assert_called_once_with("test", "c1", None)
        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "channel" in reply.content.lower()
        assert "unbound" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_channel_level_topic_status(self) -> None:
        """Channel-level /topic query when thread_id is None."""
        msg = make_command_msg(thread_id=None, chat_id="c1")
        cmd = TopicCommand(action="topic")
        bus = make_command_bus()

        topic_resolver = MagicMock()
        topic_resolver.resolve_topic = AsyncMock(return_value=None)

        await handle_topic_command(msg, cmd, bus, topic_resolver=topic_resolver)

        topic_resolver.resolve_topic.assert_called_once_with("test", "c1", None)
        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "channel" in reply.content.lower()
        assert "defaults" in reply.content.lower()
