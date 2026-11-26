"""Resolve an inbound IM message to the Web chat that carries its goal.

[INPUT]
- app.channels.types::InboundMessage (POS: Channel system domain types — pure data definitions, no I/O.)
- app.core.channel_bridge.command_session::resolve_command_session_key (POS: the session key of the conversation a command acts on)
- app.services.chat.chat_service::ChatService (POS: channel chat lookup and creation)

[OUTPUT]
- resolve_chat_id: DB chat id of the conversation a message is sent in, or None while nothing was said there

[POS]
The one place that maps a channel conversation to the chat whose goals ``/goal`` and ``/subgoal`` act on: the one
``/undo`` and the next turn use, so a goal is never set on one conversation while the work runs in another.
"""

from __future__ import annotations

from typing import Literal, overload

from app.channels.types import InboundMessage
from app.core.channel_bridge.command_session import resolve_command_session_key
from app.services.chat.chat_service import ChatService


@overload
async def resolve_chat_id(msg: InboundMessage, *, create: Literal[True]) -> str: ...


@overload
async def resolve_chat_id(msg: InboundMessage, *, create: Literal[False] = False) -> str | None: ...


async def resolve_chat_id(msg: InboundMessage, *, create: bool = False) -> str | None:
    """The chat storing the conversation ``msg`` is sent in; ``create`` opens it when nothing was said there yet.

    ``None`` when there is no such chat: nothing was said yet and ``create`` is off.
    """
    session_key = await resolve_command_session_key(msg)
    if create:
        return (await ChatService.get_or_create_channel_chat(session_key, msg.channel)).id
    chat = await ChatService.get_channel_chat_by_key(session_key)
    return chat.id if chat else None
