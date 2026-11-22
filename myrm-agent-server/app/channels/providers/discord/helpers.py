"""Helper functions for Discord channel integration.

[INPUT]
- channels.types.messages::OutboundMessage, MediaAttachment (POS: core message and media types)

[OUTPUT]
- build_discord_embed: converts a Myrm message's content into a discord.Embed
- DiscordMedia / build_discord_media: split media attachments into uploads, unfurled links and unsendable ones
- is_forum_channel / derive_thread_name: forum-channel detection and thread titles from message text
- reply_reference: native reply target (message id plus channel id) for an outbound reply

[POS]
Pure-function helpers for the Discord channel. Converts framework message objects to Discord native objects.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeGuard

import discord

from app.channels.types.messages import OutboundMessage

if TYPE_CHECKING:
    from app.channels.types.messages import MediaAttachment

logger = logging.getLogger(__name__)


def build_discord_embed(msg: OutboundMessage) -> discord.Embed | None:
    """Build a Discord Embed if the message has reasoning or tool steps."""
    if not msg.reasoning and not msg.tool_steps:
        return None

    embed = discord.Embed(color=discord.Color.blurple())

    if msg.reasoning:
        # Discord embed description limit is 4096
        reasoning_text = msg.reasoning[:4090] + "..." if len(msg.reasoning) > 4096 else msg.reasoning
        embed.add_field(name=" Thinking Process", value=f"```\n{reasoning_text}\n```", inline=False)

    if msg.tool_steps:
        steps_text = "\n".join(f"• **{step.label}**" + (f": {step.detail}" if step.detail else "") for step in msg.tool_steps)
        steps_text = steps_text[:1020] + "..." if len(steps_text) > 1024 else steps_text
        embed.add_field(name=" Tool Execution", value=steps_text, inline=False)

    return embed


@dataclass(frozen=True, slots=True)
class DiscordMedia:
    """Outbound attachments sorted by how Discord can take them."""

    files: list[discord.File]
    """Local files, uploaded with the message."""
    links: list[str]
    """URL-only attachments: posted as links, which Discord unfurls into previews."""
    failed: list[str]
    """Display names of attachments that cannot be sent at all (unreadable file, no source)."""


def build_discord_media(media: Sequence[MediaAttachment]) -> DiscordMedia:
    """Sort MediaAttachments into uploads, links and unsendable ones."""
    files: list[discord.File] = []
    links: list[str] = []
    failed: list[str] = []
    for m in media:
        if m.path:
            try:
                files.append(discord.File(fp=m.path, filename=m.filename))
            except OSError as exc:
                logger.warning("Discord attachment %s is unreadable: %s", m.display_name, exc)
                failed.append(m.display_name)
        elif m.url:
            links.append(m.url)
        else:
            failed.append(m.display_name)
    return DiscordMedia(files, links, failed)


def is_forum_channel(channel: discord.abc.Messageable | None) -> TypeGuard[discord.ForumChannel]:
    """Check whether *channel* is a Discord Forum channel (type 15).

    Uses a dual check (isinstance + raw type value) for compatibility
    across discord.py versions where ForumChannel may not exist.
    """
    if channel is None:
        return False
    forum_cls = getattr(discord, "ForumChannel", None)
    if forum_cls and isinstance(channel, forum_cls):
        return True
    channel_type = getattr(channel, "type", None)
    if channel_type is not None:
        type_value = getattr(channel_type, "value", channel_type)
        if type_value == 15:
            return True
    return False


def derive_thread_name(content: str) -> str:
    """Extract a thread title from message content (first non-empty line, max 100 chars)."""
    for line in content.split("\n"):
        cleaned = line.strip().lstrip("#").strip()
        if cleaned:
            return cleaned[:100]
    return "New Post"


def reply_reference(channel: discord.abc.Messageable, reply_to_id: str) -> discord.MessageReference | None:
    """Native Discord reply target for ``reply_to_id`` inside ``channel``.

    Discord needs the channel id next to the message id. ``None`` means the id cannot be addressed
    (not a snowflake, or a channel without an id), so the message goes out as a plain post.
    """
    channel_id = getattr(channel, "id", None)
    if not isinstance(channel_id, int):
        return None
    try:
        message_id = int(reply_to_id)
    except ValueError:
        return None
    return discord.MessageReference(message_id=message_id, channel_id=channel_id, fail_if_not_exists=False)
