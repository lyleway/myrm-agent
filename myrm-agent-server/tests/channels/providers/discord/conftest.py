"""Fixtures shared by the Discord channel tests."""

from __future__ import annotations

import pytest


@pytest.fixture
def config():
    pytest.importorskip("discord")
    from app.channels.providers.discord.config import DiscordChannelConfig

    return DiscordChannelConfig(
        bot_token="test_token",
        enable_gateway=True,
        allowed_users=["123"],
        allowed_guilds=["456"],
    )


@pytest.fixture
def channel(config):
    from app.channels.providers.discord.channel import DiscordChannel

    return DiscordChannel(config)
