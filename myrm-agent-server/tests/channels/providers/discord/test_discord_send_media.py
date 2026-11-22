"""Media attachments in Discord ``send()``: uploads, links and files that cannot be read."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("discord")
import discord

from app.channels.types import OutboundMessage
from tests.channels.providers.discord.send_testkit import make_mock_channel, make_mock_messageable, make_msg


class TestDiscordSendMedia:
    """Tests for media attachment handling in send()."""

    @pytest.mark.asyncio
    async def test_send_with_media(self) -> None:
        """send() passes files from message.media to channel.send()."""
        import os
        import tempfile

        from app.channels.types.messages import MediaAttachment, MediaType

        ch, client = make_mock_channel()
        mock_ch = make_mock_messageable()
        mock_ch.type = discord.ChannelType.text
        client.get_channel = MagicMock(return_value=mock_ch)

        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"test content")
            tmp_path = f.name
        try:
            msg = OutboundMessage(
                channel="discord",
                recipient_id="123",
                content="Here is the file",
                user_id="u1",
                media=(MediaAttachment(media_type=MediaType.DOCUMENT, path=tmp_path, filename="test.txt"),),
            )
            result = await ch.send(msg)
            assert result == "42"
            call_kwargs = mock_ch.send.call_args
            assert "files" in call_kwargs.kwargs or (len(call_kwargs.args) > 1)
            files_arg = call_kwargs.kwargs.get("files", call_kwargs.args[1] if len(call_kwargs.args) > 1 else None)
            assert isinstance(files_arg, list)
            assert len(files_arg) == 1
            assert isinstance(files_arg[0], discord.File)
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_send_without_media(self) -> None:
        """send() without media uses MISSING for files (no files sent)."""
        ch, client = make_mock_channel()
        mock_ch = make_mock_messageable()
        mock_ch.type = discord.ChannelType.text
        client.get_channel = MagicMock(return_value=mock_ch)

        msg = make_msg(content="No media")
        result = await ch.send(msg)
        assert result == "42"
        call_kwargs = mock_ch.send.call_args
        files_arg = call_kwargs.kwargs.get("files", discord.utils.MISSING)
        assert files_arg is discord.utils.MISSING

    @pytest.mark.asyncio
    async def test_make_discord_file_from_path(self) -> None:
        """build_discord_media uploads a local path as discord.File."""
        import os
        import tempfile

        from app.channels.providers.discord.helpers import build_discord_media
        from app.channels.types.messages import MediaAttachment, MediaType

        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            f.write(b"a,b,c")
            tmp_path = f.name
        try:
            media = build_discord_media((MediaAttachment(media_type=MediaType.DOCUMENT, path=tmp_path, filename="data.csv"),))
            assert len(media.files) == 1
            assert isinstance(media.files[0], discord.File)
            assert media.files[0].filename == "data.csv"
            assert (media.links, media.failed) == ([], [])
            media.files[0].close()
        finally:
            os.unlink(tmp_path)

    @pytest.mark.asyncio
    async def test_url_only_attachment_becomes_a_link(self) -> None:
        """A URL-only attachment is not downloaded: it is posted as a link Discord unfurls."""
        from app.channels.providers.discord.helpers import build_discord_media
        from app.channels.types.messages import MediaAttachment, MediaType

        media = build_discord_media(
            (MediaAttachment(media_type=MediaType.IMAGE, url="https://example.com/img.png", filename="img.png"),)
        )
        assert (media.files, media.links, media.failed) == ([], ["https://example.com/img.png"], [])

    @pytest.mark.asyncio
    async def test_unreadable_path_is_reported_as_failed(self) -> None:
        """A missing file must not abort the whole message: it is listed as failed."""
        from app.channels.providers.discord.helpers import build_discord_media
        from app.channels.types.messages import MediaAttachment, MediaType

        media = build_discord_media(
            (MediaAttachment(media_type=MediaType.DOCUMENT, path="/nonexistent/file.txt", filename="file.txt"),)
        )
        assert (media.files, media.links, media.failed) == ([], [], ["file.txt"])

    @pytest.mark.asyncio
    async def test_attachment_without_a_source_is_reported_as_failed(self) -> None:
        from app.channels.providers.discord.helpers import build_discord_media
        from app.channels.types.messages import MediaAttachment, MediaType

        media = build_discord_media((MediaAttachment(media_type=MediaType.DOCUMENT, filename="orphan.txt"),))
        assert (media.files, media.links, media.failed) == ([], [], ["orphan.txt"])
