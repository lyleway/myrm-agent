"""Cross-platform session handoff service.

[INPUT]
- database.models.chat::Chat (POS: session record with channel_session_key)
- database.models.channel::ChannelPairingModel (POS: external identity mapping)
- channels.types.session::SessionKey, started_session_key (POS: session identity and the key of a conversation opened on request)

[OUTPUT]
- HandoffResult: result dataclass
- handoff_chat: transfer a Chat session to a different channel

[POS]
Rebinds a Chat's channel_session_key to point at a target channel,
so subsequent messages on that channel resume the same conversation. The chat is stored as a conversation
the person opened on the target channel, which the target peer's reset policy lets continue like any other.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select

from app.channels.types.session import SessionKey, started_session_key
from app.database.connection import get_session
from app.database.models.channel import ChannelPairingModel
from app.database.models.chat import Chat

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HandoffResult:
    success: bool
    error: str = ""
    target_channel: str = ""
    target_session_key: str = ""


def _build_target_session_key(
    target_channel: str,
    peer_id: str,
    agent_id: str | None = None,
) -> str:
    """The key the handed chat is stored under: a conversation opened now on the target peer.

    ``resolve_session_key`` resolves the target peer's next message to the newest such conversation for as long as the
    peer's reset policy lets it continue, so the handoff works the same under every policy.
    """
    sk = SessionKey(
        channel=target_channel,
        peer_kind="dm",
        peer_id=peer_id,
        agent_id=agent_id,
    )
    return started_session_key(sk.to_str(), datetime.now(tz=UTC))


async def handoff_chat(chat_id: str, target_channel_name: str) -> HandoffResult:
    """Transfer a chat session to a target channel.

    Validates preconditions (chat exists, target channel connected, user paired),
    resolves UNIQUE constraint conflicts, and rebinds the Chat record.
    """
    from app.core.channel_bridge import channel_gateway

    async with get_session() as session:
        chat = (await session.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
        if not chat:
            return HandoffResult(success=False, error=f"Chat {chat_id} not found")

        target_ch = channel_gateway.bus.get_channel(target_channel_name)
        if not target_ch:
            return HandoffResult(
                success=False,
                error=f"Channel '{target_channel_name}' not found",
                target_channel=target_channel_name,
            )
        if not target_ch.is_connected:
            return HandoffResult(
                success=False,
                error=f"Channel '{target_channel_name}' is not connected",
                target_channel=target_channel_name,
            )

        if chat.source == target_channel_name and chat.channel_session_key:
            sk = SessionKey.parse(chat.channel_session_key)
            if sk and sk.channel == target_channel_name:
                return HandoffResult(
                    success=False,
                    error="Already on this channel",
                    target_channel=target_channel_name,
                )

        pairing = (
            await session.execute(
                select(ChannelPairingModel)
                .where(
                    ChannelPairingModel.channel == target_channel_name,
                    ChannelPairingModel.status == "active",
                )
                .order_by(ChannelPairingModel.created_at.desc())
                .limit(1)
            )
        ).scalar_one_or_none()

        if not pairing:
            return HandoffResult(
                success=False,
                error=f"No pairing for channel '{target_channel_name}'",
                target_channel=target_channel_name,
            )

        target_key = _build_target_session_key(
            target_channel_name,
            pairing.sender_id,
            agent_id=chat.agent_id,
        )

        # Unbind any Chat currently holding target_key to avoid UNIQUE conflict
        existing = (
            await session.execute(
                select(Chat).where(
                    Chat.channel_session_key == target_key,
                    Chat.id != chat_id,
                )
            )
        ).scalar_one_or_none()

        if existing:
            logger.info(
                "Handoff: unbinding Chat %s from key %s (replaced by %s)",
                existing.id,
                target_key,
                chat_id,
            )
            existing.channel_session_key = None
            await session.flush()

        chat.channel_session_key = target_key
        chat.source = target_channel_name

        await session.commit()

    logger.info(
        "Handoff: chat %s -> channel %s (key=%s)",
        chat_id,
        target_channel_name,
        target_key,
    )
    return HandoffResult(
        success=True,
        target_channel=target_channel_name,
        target_session_key=target_key,
    )
