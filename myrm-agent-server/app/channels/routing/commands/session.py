"""[INPUT]
- app.channels.i18n::get_text (POS: Provides multi-language support for gateway slash command replies and system-generated channel messages (not Agent LLM output).)
- app.channels.protocols.compact::MAX_FOCUS_TOPIC_LENGTH, CompactSkip (POS: Business-layer handler protocol for the /compact slash command; ``CompactSkip`` names why nothing was compacted, in terms a reply can word.)
- app.channels.types::InboundMessage / OutboundMessage (POS: Channel system domain types — pure data definitions, no I/O.)
- app.core.channel_bridge.command_session::start_conversation (POS: The conversation an IM command acts on, and the opening of a new one.)

[OUTPUT]
- start_new_conversation: Open a new conversation for the chat of a message; when that fails, tell the sender and report it.
- handle_new_session: Handle /new command: open the new conversation, then confirm it.
- handle_compact: Handle /compact command: compress chat context to reduce token cost.

[POS]
IM 斜杠命令域的子模块；由 app/channels/routing/commands/__init__.py 聚合导出。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.channels.i18n import get_text
from app.channels.protocols.compact import MAX_FOCUS_TOPIC_LENGTH, CompactSkip
from app.channels.types import (
    InboundMessage,
    OutboundMessage,
)

if TYPE_CHECKING:
    from app.channels.core.bus import MessageBus
    from app.channels.protocols.compact import CompactHandler
    from app.channels.routing.policy_resolver import (
        PolicyResolver,
    )

logger = logging.getLogger(__name__)

# The catalog message that words each way a compaction can fail to happen; the failure message also covers a handler that raised.
_COMPACT_SKIP_MESSAGES: dict[CompactSkip, str] = {
    CompactSkip.NOTHING_TO_COMPACT: "compact_skipped_nothing",
    CompactSkip.BUSY: "compact_skipped_busy",
    CompactSkip.PAUSED: "compact_skipped_paused",
    CompactSkip.FAILED: "compact_failed",
}


async def _reply_to_sender(msg: InboundMessage, bus: MessageBus, content: str) -> None:
    """Answer the chat ``msg`` came from."""
    reply = OutboundMessage(
        channel=msg.channel,
        recipient_id=msg.chat_id or msg.sender_id,
        content=content,
        user_id=msg.user_id or "",
        thread_id=msg.thread_id,
        reply_to_id=msg.message_id if msg.is_group else None,
    )
    await bus.publish_outbound(reply)


async def start_new_conversation(msg: InboundMessage, bus: MessageBus) -> bool:
    """Open a new conversation for the chat ``msg`` came from; when that fails, tell the sender and return False.

    The failure is answered, not retried: the sender asked for a clean start and has to know they do not have one.
    """
    from app.core.channel_bridge.command_session import start_conversation

    try:
        await start_conversation(msg)
    except Exception:
        logger.warning(
            "AgentRouter: could not start a new conversation for %s/%s", msg.channel, msg.chat_id or msg.sender_id, exc_info=True
        )
        await _reply_to_sender(msg, bus, get_text(msg, "new_session_failed"))
        return False
    return True


async def handle_new_session(msg: InboundMessage, bus: MessageBus) -> None:
    """Handle /new command: open the new conversation, then confirm it.

    The confirmation follows the opening, so the sender's next message, which may arrive at once, belongs to it.
    """
    if await start_new_conversation(msg, bus):
        await _reply_to_sender(msg, bus, get_text(msg, "new_session_started"))


async def handle_compact(
    msg: InboundMessage,
    bus: MessageBus,
    resolver: PolicyResolver,
    compact_handler: CompactHandler | None = None,
    *,
    focus_topic: str = "",
) -> None:
    """Handle /compact command: compress chat context to reduce token cost.

    When compact_handler is None, replies with "Compaction not configured."
    Business layer provides CompactHandler to perform DB/chat/compact operations.
    """
    peer_id = msg.chat_id or msg.sender_id
    effective_topic = focus_topic[:MAX_FOCUS_TOPIC_LENGTH].strip() if focus_topic else ""

    if not compact_handler:
        content = get_text(msg, "compact_not_configured")
        reply = OutboundMessage(
            channel=msg.channel,
            recipient_id=peer_id,
            content=content,
            user_id=msg.user_id or "",
            thread_id=msg.thread_id,
            reply_to_id=((msg.message_id or str(msg.metadata.get("message_id", ""))) if msg.is_group else None),
        )
        await bus.publish_outbound(reply)
        return

    try:
        if msg.is_group:
            resolved = await resolver.resolve_group_user(msg)
            if not resolved:
                return
            user_id, resolved_msg = resolved
            msg = resolved_msg
        else:
            dm_uid = await resolver.resolve_dm_user(msg)
            if not dm_uid:
                return
            user_id = dm_uid

        result = await compact_handler(msg, user_id, focus_topic=effective_topic)

        if result.compacted:
            topic_hint = ""
            if effective_topic:
                topic_hint = " " + get_text(msg, "compact_topic_hint", topic=effective_topic)
            content = get_text(
                msg,
                "compact_success",
                message_count=result.message_count,
                tokens_saved=result.tokens_saved,
                topic_hint=topic_hint,
            )
        else:
            content = get_text(msg, _COMPACT_SKIP_MESSAGES[result.skip or CompactSkip.FAILED])
    except Exception as exc:
        logger.warning("AgentRouter: /compact failed for %s/%s: %s", msg.channel, peer_id, exc)
        content = get_text(msg, _COMPACT_SKIP_MESSAGES[CompactSkip.FAILED])

    reply = OutboundMessage(
        channel=msg.channel,
        recipient_id=peer_id,
        content=content,
        user_id=msg.user_id or "",
        thread_id=msg.thread_id,
        reply_to_id=((msg.message_id or str(msg.metadata.get("message_id", ""))) if msg.is_group else None),
    )
    await bus.publish_outbound(reply)
