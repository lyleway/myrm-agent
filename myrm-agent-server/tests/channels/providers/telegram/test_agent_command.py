"""Unit tests for /agent command handling in TelegramChannel.

Tests cover:
- /agent text command → picker sent + message suppressed
- /agent@botname variant
- ag: callback → editMessageText + /bind conversion
- Edge cases: empty agent_id, missing origin_message_id
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from unittest.mock import AsyncMock, patch

import pytest

from app.channels.providers.telegram.channel import TelegramChannel
from app.channels.types import TopicContext
from app.channels.types.messages import InboundMessage


def _make_msg(
    content: str = "",
    metadata: dict[str, object] | None = None,
    chat_id: str | None = "12345",
    sender_id: str = "user1",
    thread_id: str | None = None,
) -> InboundMessage:
    return InboundMessage(
        channel="telegram",
        sender_id=sender_id,
        content=content,
        chat_id=chat_id,
        metadata=metadata or {},
        thread_id=thread_id,
    )


@dataclass
class FakeAgentProfile:
    id: str
    display_name: str | None = None


@pytest.fixture
def client() -> AsyncMock:
    """The Bot API client of the channel under test."""
    return AsyncMock()


@pytest.fixture
def channel(client: AsyncMock) -> TelegramChannel:
    """A TelegramChannel built without its constructor, talking to the mocked ``client``."""
    ch = TelegramChannel.__new__(TelegramChannel)
    ch._client = client
    ch._bot_username = "testbot"
    return ch


class TestHandleAgentCommand:
    """Test _handle_agent_command dispatch logic."""

    @pytest.fixture(autouse=True)
    def picker(self, channel: TelegramChannel) -> Iterator[AsyncMock]:
        """``_send_agent_picker`` of the channel, replaced by a mock."""
        with patch.object(channel, "_send_agent_picker", AsyncMock()) as send_picker:
            yield send_picker

    @pytest.mark.asyncio
    async def test_agent_command_sends_picker(self, channel: TelegramChannel, picker: AsyncMock) -> None:
        msg = _make_msg(content="/agent")
        result = await channel._handle_agent_command(msg)
        assert result is None
        picker.assert_awaited_once_with(msg)

    @pytest.mark.asyncio
    async def test_agent_command_with_bot_suffix(self, channel: TelegramChannel, picker: AsyncMock) -> None:
        msg = _make_msg(content="/agent@TestBot")
        result = await channel._handle_agent_command(msg)
        assert result is None
        picker.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_agent_command_case_insensitive(self, channel: TelegramChannel) -> None:
        msg = _make_msg(content="/AGENT@TESTBOT")
        result = await channel._handle_agent_command(msg)
        assert result is None

    @pytest.mark.asyncio
    async def test_non_agent_message_passes_through(self, channel: TelegramChannel) -> None:
        msg = _make_msg(content="Hello world")
        result = await channel._handle_agent_command(msg)
        assert result is msg

    @pytest.mark.asyncio
    async def test_ag_callback_converts_to_bind(self, channel: TelegramChannel) -> None:
        msg = _make_msg(
            content="my-agent-id",
            metadata={"callback_prefix": "ag", "origin_message_id": 999},
        )
        with patch(
            "app.services.agent.agent_service.AgentService.get_agent_by_id",
            new_callable=AsyncMock,
            return_value=FakeAgentProfile(id="my-agent-id", display_name="My Agent"),
        ):
            result = await channel._handle_agent_command(msg)

        assert result is not None
        assert result.content == "/bind my-agent-id"

    @pytest.mark.asyncio
    async def test_ag_callback_edits_picker_message(self, channel: TelegramChannel, client: AsyncMock) -> None:
        msg = _make_msg(
            content="agent-x",
            metadata={"callback_prefix": "ag", "origin_message_id": 42},
        )
        with patch(
            "app.services.agent.agent_service.AgentService.get_agent_by_id",
            new_callable=AsyncMock,
            return_value=FakeAgentProfile(id="agent-x", display_name="Agent X"),
        ):
            await channel._handle_agent_command(msg)

        client.edit_message_text.assert_awaited_once()
        call_args = client.edit_message_text.call_args
        assert call_args[0][0] == "12345"
        assert call_args[0][1] == 42
        assert "Agent X" in call_args[0][2]
        assert call_args.kwargs.get("reply_markup") == {"inline_keyboard": []}

    @pytest.mark.asyncio
    async def test_ag_callback_no_origin_message_skips_edit(self, channel: TelegramChannel, client: AsyncMock) -> None:
        msg = _make_msg(
            content="agent-y",
            metadata={"callback_prefix": "ag", "origin_message_id": None},
        )
        result = await channel._handle_agent_command(msg)
        assert result is not None
        assert result.content == "/bind agent-y"
        client.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_ag_callback_empty_agent_id_passes_through(self, channel: TelegramChannel) -> None:
        msg = _make_msg(content="", metadata={"callback_prefix": "ag"})
        result = await channel._handle_agent_command(msg)
        assert result is msg

    @pytest.mark.asyncio
    async def test_picker_failure_still_suppresses(self, channel: TelegramChannel, picker: AsyncMock) -> None:
        picker.side_effect = RuntimeError("API down")
        msg = _make_msg(content="/agent")
        result = await channel._handle_agent_command(msg)
        assert result is None


def _topic(agent_id: str | None, *, revoked: bool = False) -> TopicContext:
    return TopicContext(topic_id="t", agent_id=agent_id, identity_revoked=revoked)


class _TopicsByLevel:
    """Bindings by ``(chat_id, thread_id)``; the channel-wide binding is stored under the chat id ``__global__``."""

    def __init__(self, bindings: dict[tuple[str, str | None], TopicContext] | None = None) -> None:
        self._bindings = bindings or {}

    async def resolve_topic(self, channel: str, chat_id: str, thread_id: str | None) -> TopicContext | None:
        return self._bindings.get((chat_id, thread_id))


class TestSendAgentPicker:
    """Test _send_agent_picker inline keyboard construction."""

    @pytest.mark.asyncio
    async def test_picker_sends_keyboard(self, channel: TelegramChannel, client: AsyncMock) -> None:
        agents = [
            FakeAgentProfile(id="a1", display_name="Alpha"),
            FakeAgentProfile(id="a2", display_name="Beta"),
        ]
        msg = _make_msg(content="/agent", thread_id="77")

        with (
            patch(
                "app.services.agent.agent_service.AgentService.get_agent_list",
                new_callable=AsyncMock,
                return_value=(agents, 2),
            ),
            patch(
                "app.core.channel_bridge.topic_config.SqlTopicManager",
                return_value=_TopicsByLevel(),
            ),
        ):
            await channel._send_agent_picker(msg)

        client.send_message.assert_awaited_once()
        call_args = client.send_message.call_args

        assert call_args[0][0] == "12345"
        reply_markup = call_args.kwargs.get("reply_markup")
        assert reply_markup is not None
        keyboard = reply_markup["inline_keyboard"]
        assert len(keyboard) == 2
        assert keyboard[0][0]["callback_data"] == "ag:a1"
        assert keyboard[1][0]["callback_data"] == "ag:a2"

    @pytest.mark.asyncio
    async def test_picker_no_agents_sends_message(self, channel: TelegramChannel, client: AsyncMock) -> None:
        msg = _make_msg(content="/agent")

        with (
            patch(
                "app.services.agent.agent_service.AgentService.get_agent_list",
                new_callable=AsyncMock,
                return_value=([], 0),
            ),
            patch(
                "app.core.channel_bridge.topic_config.SqlTopicManager",
                return_value=_TopicsByLevel(),
            ),
        ):
            await channel._send_agent_picker(msg)

        client.send_message.assert_awaited_once()
        args = client.send_message.call_args[0]
        assert "No agents" in args[1] or "未配置" in args[1]

    @pytest.mark.asyncio
    async def test_picker_no_chat_id_returns_early(self, channel: TelegramChannel, client: AsyncMock) -> None:
        msg = _make_msg(content="/agent", chat_id=None, sender_id="")

        with (
            patch(
                "app.services.agent.agent_service.AgentService.get_agent_list",
                new_callable=AsyncMock,
                return_value=([FakeAgentProfile(id="x")], 1),
            ),
            patch(
                "app.core.channel_bridge.topic_config.SqlTopicManager",
                return_value=_TopicsByLevel(),
            ),
        ):
            await channel._send_agent_picker(msg)

        client.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_picker_uses_id_as_fallback_label(self, channel: TelegramChannel, client: AsyncMock) -> None:
        agents = [FakeAgentProfile(id="raw-id", display_name=None)]
        msg = _make_msg(content="/agent")

        with (
            patch(
                "app.services.agent.agent_service.AgentService.get_agent_list",
                new_callable=AsyncMock,
                return_value=(agents, 1),
            ),
            patch(
                "app.core.channel_bridge.topic_config.SqlTopicManager",
                return_value=_TopicsByLevel(),
            ),
        ):
            await channel._send_agent_picker(msg)

        call_kwargs = client.send_message.call_args.kwargs
        reply_markup = call_kwargs.get("reply_markup")
        assert reply_markup["inline_keyboard"][0][0]["text"] == "raw-id"

    @pytest.mark.asyncio
    async def test_picker_marks_bound_agent_with_checkmark(self, channel: TelegramChannel, client: AsyncMock) -> None:
        agents = [
            FakeAgentProfile(id="a1", display_name="Alpha"),
            FakeAgentProfile(id="a2", display_name="Beta"),
        ]
        msg = _make_msg(content="/agent")

        with (
            patch(
                "app.services.agent.agent_service.AgentService.get_agent_list",
                new_callable=AsyncMock,
                return_value=(agents, 2),
            ),
            patch(
                "app.core.channel_bridge.topic_config.SqlTopicManager",
                return_value=_TopicsByLevel({("12345", None): _topic("a2")}),
            ),
        ):
            await channel._send_agent_picker(msg)

        call_kwargs = client.send_message.call_args.kwargs
        keyboard = call_kwargs["reply_markup"]["inline_keyboard"]
        assert keyboard[0][0]["text"] == "Alpha"
        assert keyboard[1][0]["text"] == "✅ Beta"

    @pytest.mark.asyncio
    async def test_picker_no_bound_agent_no_checkmark(self, channel: TelegramChannel, client: AsyncMock) -> None:
        agents = [FakeAgentProfile(id="a1", display_name="Alpha")]
        msg = _make_msg(content="/agent")

        with (
            patch(
                "app.services.agent.agent_service.AgentService.get_agent_list",
                new_callable=AsyncMock,
                return_value=(agents, 1),
            ),
            patch(
                "app.core.channel_bridge.topic_config.SqlTopicManager",
                return_value=_TopicsByLevel(),
            ),
        ):
            await channel._send_agent_picker(msg)

        call_kwargs = client.send_message.call_args.kwargs
        keyboard = call_kwargs["reply_markup"]["inline_keyboard"]
        assert keyboard[0][0]["text"] == "Alpha"
        assert "✅" not in keyboard[0][0]["text"]

    async def _picker_labels(
        self, channel: TelegramChannel, client: AsyncMock, topics: _TopicsByLevel, msg: InboundMessage
    ) -> list[str]:
        agents = [FakeAgentProfile(id="a1", display_name="Alpha"), FakeAgentProfile(id="a2", display_name="Beta")]
        with (
            patch(
                "app.services.agent.agent_service.AgentService.get_agent_list",
                new_callable=AsyncMock,
                return_value=(agents, 2),
            ),
            patch("app.core.channel_bridge.topic_config.SqlTopicManager", return_value=topics),
        ):
            await channel._send_agent_picker(msg)
        keyboard = client.send_message.call_args.kwargs["reply_markup"]["inline_keyboard"]
        return [row[0]["text"] for row in keyboard]

    @pytest.mark.asyncio
    async def test_picker_marks_the_agent_of_the_thread_binding_over_the_chat_binding(
        self, channel: TelegramChannel, client: AsyncMock
    ) -> None:
        topics = _TopicsByLevel({("12345", "77"): _topic("a1"), ("12345", None): _topic("a2")})

        labels = await self._picker_labels(channel, client, topics, _make_msg(content="/agent", thread_id="77"))

        assert labels == ["✅ Alpha", "Beta"]

    @pytest.mark.asyncio
    async def test_picker_marks_the_agent_of_a_channel_wide_binding(self, channel: TelegramChannel, client: AsyncMock) -> None:
        topics = _TopicsByLevel({("__global__", None): _topic("a2")})

        labels = await self._picker_labels(channel, client, topics, _make_msg(content="/agent"))

        assert labels == ["Alpha", "✅ Beta"]

    @pytest.mark.asyncio
    async def test_picker_marks_nothing_when_the_thread_binding_names_no_agent(
        self, channel: TelegramChannel, client: AsyncMock
    ) -> None:
        """The thread binding serves the message even without an agent, so the chat's agent is not the one in use."""
        topics = _TopicsByLevel({("12345", "77"): _topic(None), ("12345", None): _topic("a2")})

        labels = await self._picker_labels(channel, client, topics, _make_msg(content="/agent", thread_id="77"))

        assert labels == ["Alpha", "Beta"]

    @pytest.mark.asyncio
    async def test_picker_marks_nothing_for_a_frozen_identity(self, channel: TelegramChannel, client: AsyncMock) -> None:
        topics = _TopicsByLevel({("12345", None): _topic("a2", revoked=True)})

        labels = await self._picker_labels(channel, client, topics, _make_msg(content="/agent"))

        assert labels == ["Alpha", "Beta"]
