"""The approval card an IM chat's peer receives when one of that chat's approvals is requested.

[INPUT]
- app.database.models.chat::Chat (POS: 聊天持久化模型：来源渠道与渠道会话键)
- app.core.notifications.approval_card::build_approval_card (POS: 审批卡文案，按发送路由决定是否带按钮)
- app.core.channel_bridge::get_channel_gateway (POS: 渠道出站网关)
- app.core.channel_bridge.locale_provider::resolve_user_locale (POS: 所有者的语言偏好)

[OUTPUT]
- push_approval_card_to_chat_peer: send the card to the IM peer of a chat (best effort, never raises)

[POS]
Registry-side delivery of approval cards to IM peers; web chats have no peer and get nothing here.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.database.connection import get_session

if TYPE_CHECKING:
    from app.database.models.approval import ApprovalRecord

logger = logging.getLogger(__name__)


async def push_approval_card_to_chat_peer(record: ApprovalRecord, chat_id: str) -> None:
    """Send the approval card to the IM peer of ``chat_id``; web chats have no peer. Best effort: never raises.

    It is the notification dispatcher's card: in the owner's language, with decision buttons only where a click
    can be resolved. It is built from the request's identity, severity and reason only: the payload (tool
    arguments, drafted text) is not for whoever else is in the chat.
    """
    try:
        from app.database.models.chat import Chat

        async with get_session() as db:
            chat = (await db.execute(select(Chat).where(Chat.id == chat_id))).scalar_one_or_none()
        if not (chat and chat.source and chat.source != "web" and chat.channel_session_key):
            return

        from app.channels.types.messages import MessagePriority, OutboundMessage
        from app.channels.types.session import SessionKey
        from app.core.channel_bridge import get_channel_gateway
        from app.core.channel_bridge.locale_provider import resolve_user_locale
        from app.core.notifications.approval_card import build_approval_card

        # The stored key may carry an epoch suffix (``:e=...``) that is not part of the peer's address.
        session_key = SessionKey.parse(chat.channel_session_key.split(":e=")[0])
        if not (session_key and session_key.peer_id):
            return

        content, components = build_approval_card(
            {
                "approval_id": record.id,
                "action_type": record.action_type,
                "severity": record.severity,
                "reason": record.reason,
            },
            session_key.channel,
            await resolve_user_locale(),
        )
        await get_channel_gateway().publish(
            OutboundMessage(
                channel=session_key.channel,
                recipient_id=session_key.peer_id,
                user_id="sandbox",
                content=content,
                components=components,
                priority=MessagePriority.SYSTEM,
            )
        )
        logger.info("Pushed approval card to channel '%s' for recipient '%s'", session_key.channel, session_key.peer_id)
    except Exception as e:
        logger.error("Failed to push approval card to channel: %s", e)
