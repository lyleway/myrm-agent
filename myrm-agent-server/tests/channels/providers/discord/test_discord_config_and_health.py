"""Tests for the Discord channel configuration, credential spec and health check."""

from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("discord")

from app.channels.providers.discord.channel import (
    DiscordChannel,
)
from app.channels.providers.discord.config import (
    DiscordChannelConfig,
)
from app.channels.types import ChannelStatus


class TestParseAllowedChannels:
    def test_valid_entries(self) -> None:
        result = DiscordChannelConfig._parse_allowed_channels(["111:222", "333:444"])
        assert result == [("111", "222"), ("333", "444")]

    def test_entries_with_whitespace(self) -> None:
        result = DiscordChannelConfig._parse_allowed_channels([" 111 : 222 ", "333:444"])
        assert result == [("111", "222"), ("333", "444")]

    def test_invalid_entries_skipped(self) -> None:
        result = DiscordChannelConfig._parse_allowed_channels(["111", "222:333:444", ":555", "666:", "777:888"])
        assert result == [("777", "888")]

    def test_none_returns_empty(self) -> None:
        result = DiscordChannelConfig._parse_allowed_channels(None)
        assert result == []

    def test_empty_list_returns_empty(self) -> None:
        result = DiscordChannelConfig._parse_allowed_channels([])
        assert result == []


class TestDiscordConfigVoiceFields:
    def test_voice_follow_users(self) -> None:
        cfg = DiscordChannelConfig(
            bot_token="token",
            voice_follow_users=["100", "200"],
        )
        assert cfg.voice_follow_users == ["100", "200"]

    def test_voice_follow_users_default(self) -> None:
        cfg = DiscordChannelConfig(bot_token="token")
        assert cfg.voice_follow_users == []

    def test_voice_allowed_channels_parsed(self) -> None:
        cfg = DiscordChannelConfig(
            bot_token="token",
            voice_allowed_channels=["111:222", "333:444"],
        )
        assert cfg.voice_allowed_channels == [("111", "222"), ("333", "444")]

    def test_voice_allowed_channels_default(self) -> None:
        cfg = DiscordChannelConfig(bot_token="token")
        assert cfg.voice_allowed_channels == []


class TestCredentialSpecFields:
    def test_credential_spec_includes_voice_follow_fields(self) -> None:
        spec = DiscordChannel.credential_spec
        field_keys = {name for name, _ in spec.fields}
        assert "voice_follow_users" in field_keys
        assert "voice_allowed_channels" in field_keys


class TestDiscordHealthCheck:
    """Tests for DiscordChannel.health_check REST liveness probe."""

    @pytest.fixture
    def gw_channel(self):
        cfg = DiscordChannelConfig(bot_token="tok", enable_gateway=True)
        ch = DiscordChannel(cfg)
        ch._status = ChannelStatus.RUNNING
        return ch

    @pytest.mark.asyncio
    async def test_stopped_channel_returns_false(self, gw_channel):
        gw_channel._status = ChannelStatus.STOPPED
        assert await gw_channel.health_check() is False

    @pytest.mark.asyncio
    async def test_no_gateway_mode_returns_true(self):
        cfg = DiscordChannelConfig(bot_token="tok", enable_gateway=False)
        ch = DiscordChannel(cfg)
        ch._status = ChannelStatus.RUNNING
        assert await ch.health_check() is True

    @pytest.mark.asyncio
    async def test_gateway_task_done_returns_false(self, gw_channel):
        done_task = AsyncMock()
        done_task.done.return_value = True
        gw_channel._gateway_task = done_task
        assert await gw_channel.health_check() is False
        assert gw_channel.health.consecutive_failures > 0

    @pytest.mark.asyncio
    async def test_client_closed_returns_false(self, gw_channel):
        mock_client = MagicMock()
        mock_client.is_closed.return_value = True
        gw_channel._client = mock_client
        gw_channel._gateway_task = MagicMock(done=MagicMock(return_value=False))
        assert await gw_channel.health_check() is False

    @pytest.mark.asyncio
    async def test_no_user_yet_returns_true(self, gw_channel):
        mock_client = MagicMock()
        mock_client.is_closed.return_value = False
        mock_client.user = None
        gw_channel._client = mock_client
        gw_channel._gateway_task = MagicMock(done=MagicMock(return_value=False))
        assert await gw_channel.health_check() is True

    @pytest.mark.asyncio
    async def test_rest_probe_success(self, gw_channel):
        mock_user = MagicMock()
        mock_user.id = 12345
        mock_client = MagicMock()
        mock_client.is_closed.return_value = False
        mock_client.user = mock_user
        mock_client.fetch_user = AsyncMock(return_value=mock_user)
        gw_channel._client = mock_client
        gw_channel._gateway_task = MagicMock(done=MagicMock(return_value=False))
        assert await gw_channel.health_check() is True
        mock_client.fetch_user.assert_awaited_once_with(12345)

    @pytest.mark.asyncio
    async def test_rest_probe_failure(self, gw_channel):
        mock_user = MagicMock()
        mock_user.id = 12345
        mock_client = MagicMock()
        mock_client.is_closed.return_value = False
        mock_client.user = mock_user
        mock_client.fetch_user = AsyncMock(side_effect=Exception("timeout"))
        gw_channel._client = mock_client
        gw_channel._gateway_task = MagicMock(done=MagicMock(return_value=False))
        assert await gw_channel.health_check() is False
        assert gw_channel.health.consecutive_failures > 0

    @pytest.mark.asyncio
    async def test_degraded_channel_still_probes(self, gw_channel):
        gw_channel._status = ChannelStatus.DEGRADED
        mock_user = MagicMock()
        mock_user.id = 1
        mock_client = MagicMock()
        mock_client.is_closed.return_value = False
        mock_client.user = mock_user
        mock_client.fetch_user = AsyncMock(return_value=mock_user)
        gw_channel._client = mock_client
        gw_channel._gateway_task = MagicMock(done=MagicMock(return_value=False))
        assert await gw_channel.health_check() is True
