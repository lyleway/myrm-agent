"""Discord ``send()`` delivers or raises: error classes, files and links, forum threads and non-forum channels."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("discord")
import discord

from app.channels.core.exceptions import ChannelSendError
from app.channels.providers.discord.channel import DiscordChannel
from app.channels.types import OutboundMessage
from tests.channels.providers.discord.send_testkit import make_mock_channel, make_mock_messageable, make_msg


class TestDiscordSendDelivery:
    """send() must raise instead of reporting a lost message as delivered."""

    @staticmethod
    def _channel_with(mock_ch: MagicMock) -> DiscordChannel:
        ch, client = make_mock_channel()
        mock_ch.type = discord.ChannelType.text
        client.get_channel = MagicMock(return_value=mock_ch)
        return ch

    @staticmethod
    def _http_error(status: int) -> discord.HTTPException:
        response = MagicMock()
        response.status = status
        response.reason = "error"
        return discord.HTTPException(response, "boom")

    @pytest.mark.asyncio
    async def test_unreachable_channel_raises_a_retriable_error(self) -> None:
        ch, client = make_mock_channel()
        client.get_channel = MagicMock(return_value=None)
        client.fetch_channel = AsyncMock(side_effect=RuntimeError("unknown channel"))

        with pytest.raises(ChannelSendError, match="not reachable") as excinfo:
            await ch.send(make_msg())

        assert excinfo.value.retriable is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("status", "retriable"), [(403, False), (404, False), (429, True), (503, True)])
    async def test_http_errors_carry_their_retry_class(self, status: int, retriable: bool) -> None:
        mock_ch = make_mock_messageable()
        mock_ch.send = AsyncMock(side_effect=self._http_error(status))

        with pytest.raises(ChannelSendError) as excinfo:
            await self._channel_with(mock_ch).send(make_msg())

        assert (excinfo.value.status_code, excinfo.value.retriable) == (status, retriable)

    @pytest.mark.asyncio
    async def test_other_discord_errors_are_retriable(self) -> None:
        mock_ch = make_mock_messageable()
        mock_ch.send = AsyncMock(side_effect=discord.ClientException("not connected"))

        with pytest.raises(ChannelSendError) as excinfo:
            await self._channel_with(mock_ch).send(make_msg())

        assert excinfo.value.retriable is True

    @pytest.mark.asyncio
    async def test_url_only_attachment_is_delivered_as_a_link(self) -> None:
        from app.channels.types.messages import MediaAttachment, MediaType

        mock_ch = make_mock_messageable()
        attachment = MediaAttachment(media_type=MediaType.IMAGE, url="https://example.com/img.png")
        msg = OutboundMessage(channel="discord", recipient_id="123", content="Chart", user_id="u1", media=(attachment,))

        assert await self._channel_with(mock_ch).send(msg) == "42"

        assert mock_ch.send.call_args.kwargs["content"] == "Chart\nhttps://example.com/img.png"

    @pytest.mark.asyncio
    async def test_unreadable_file_is_reported_after_the_text_was_delivered(self) -> None:
        from app.channels.types.messages import MediaAttachment, MediaType

        mock_ch = make_mock_messageable()
        attachment = MediaAttachment(media_type=MediaType.DOCUMENT, path="/nonexistent/file.txt", filename="file.txt")
        msg = OutboundMessage(channel="discord", recipient_id="123", content="Report", user_id="u1", media=(attachment,))

        with pytest.raises(ChannelSendError) as excinfo:
            await self._channel_with(mock_ch).send(msg)

        mock_ch.send.assert_called_once()
        assert excinfo.value.failed_attachments == ("file.txt",)
        assert (excinfo.value.accepted, excinfo.value.retriable) == (True, False)

    @pytest.mark.asyncio
    async def test_message_with_only_unreadable_files_sends_nothing_and_stays_retriable(self) -> None:
        from app.channels.types.messages import MediaAttachment, MediaType

        mock_ch = make_mock_messageable()
        attachment = MediaAttachment(media_type=MediaType.DOCUMENT, path="/nonexistent/file.txt", filename="file.txt")
        msg = OutboundMessage(channel="discord", recipient_id="123", content="", user_id="u1", media=(attachment,))

        with pytest.raises(ChannelSendError) as excinfo:
            await self._channel_with(mock_ch).send(msg)

        mock_ch.send.assert_not_called()
        assert (excinfo.value.accepted, excinfo.value.retriable) == (False, True)

    @staticmethod
    def _forum_channel(create_thread: AsyncMock) -> DiscordChannel:
        mock_forum = MagicMock(spec=discord.ForumChannel)
        mock_forum.type = MagicMock(value=15)
        mock_forum.flags = discord.ChannelFlags(require_tag=False)
        mock_forum.create_thread = create_thread
        ch, _ = make_mock_channel()
        ch._resolve_channel = AsyncMock(return_value=mock_forum)  # type: ignore[method-assign]
        return ch

    @pytest.mark.asyncio
    async def test_forum_thread_carries_the_files(self, tmp_path: Path) -> None:
        from app.channels.types.messages import MediaAttachment, MediaType

        report = tmp_path / "report.txt"
        report.write_text("data")
        thread = MagicMock(id=99, message=MagicMock(id=88), send=AsyncMock())
        create_thread = AsyncMock(return_value=thread)
        ch = self._forum_channel(create_thread)
        attachment = MediaAttachment(media_type=MediaType.DOCUMENT, path=str(report), filename="report.txt")
        msg = OutboundMessage(channel="discord", recipient_id="123", content="Weekly", user_id="u1", media=(attachment,))

        assert await ch.send(msg) == "88"

        files = create_thread.call_args.kwargs["files"]
        assert [f.filename for f in files] == ["report.txt"]
        for f in files:
            f.close()

    @pytest.mark.asyncio
    async def test_forum_placeholder_failure_stays_soft(self) -> None:
        ch = self._forum_channel(AsyncMock(side_effect=self._http_error(500)))

        assert await ch.send_placeholder("123", "thinking") is None


class TestDiscordNonForumSend:
    @pytest.mark.asyncio
    async def test_send_to_non_forum_guild_channel(self) -> None:
        """Non-forum GuildChannel should use normal send path."""
        ch, client = make_mock_channel()
        mock_ch = make_mock_messageable()
        mock_ch.type = discord.ChannelType.text
        client.get_channel = MagicMock(return_value=mock_ch)

        msg = make_msg(content="Normal message")
        result = await ch.send(msg)
        assert result == "42"
        mock_ch.send.assert_called()
