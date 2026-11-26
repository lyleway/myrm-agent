"""The conversation an IM command acts on, and the opening of a new one.

[INPUT]
- app.channels.routing.topic_binding::resolve_bound_topic (POS: The one cascade that picks the binding serving a message)
- app.channels.types::InboundMessage, SessionPolicy, started_session_key
- app.core.channel_bridge.agent_executor.session::build_channel_session_key, resolve_session_key (POS: The one place that decides which conversation a channel message belongs to)
- app.core.channel_bridge.config_loader::load_user_configs
- app.core.channel_bridge.config_parsers::extract_session_policy, session_policy_from_agent_dict
- app.core.channel_bridge.topic_config::SqlTopicManager
- app.services.agent.profile.profile_resolver::get_agent_profile_resolver
- app.services.chat.chat_service::ChatService (POS: Channel chat lookup and creation)

[OUTPUT]
- resolve_command_session_key: session key the conversation of a message's chat is stored under
- start_conversation: open a new conversation for the chat a message came from

[POS]
/undo, /retry, /compact, /handoff and /goal act on the conversation of the chat they are sent from, and /new opens
the next one. The agent that serves the message and the reset policy that applies decide which conversation that is, so
this resolves both the way the turn executor does (the serving binding's ``effective_agent_id``; the agent's own
policy, else the person's). A command that resolved either differently would act on another conversation than the one
the user is talking in. A conversation opened here is stored before the reply, so the next turn and every command
after it resolve to it through the same resolver.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.channels.routing.topic_binding import resolve_bound_topic
from app.channels.types import InboundMessage, SessionPolicy, started_session_key
from app.core.channel_bridge.agent_executor.session import build_channel_session_key, resolve_session_key
from app.core.channel_bridge.config_loader import load_user_configs
from app.core.channel_bridge.config_parsers import extract_session_policy, session_policy_from_agent_dict
from app.core.channel_bridge.topic_config import SqlTopicManager
from app.services.agent.profile.profile_resolver import get_agent_profile_resolver
from app.services.chat.chat_service import ChatService


async def _resolve_scope(msg: InboundMessage) -> tuple[str | None, SessionPolicy]:
    """The agent that serves ``msg`` and the reset policy its conversation follows."""
    topic = await resolve_bound_topic(SqlTopicManager(), msg)
    agent_id = topic.effective_agent_id if topic else None

    configs = await load_user_configs()
    session_policy = extract_session_policy(configs.personal_settings_dict)
    if agent_id:
        profile = await get_agent_profile_resolver().resolve(agent_id)
        if profile and profile.session_policy:
            session_policy = session_policy_from_agent_dict(profile.session_policy)
    return agent_id, session_policy


async def resolve_command_session_key(msg: InboundMessage) -> str:
    """The session key under which the turns of ``msg``'s conversation are stored right now."""
    agent_id, session_policy = await _resolve_scope(msg)
    return await resolve_session_key(msg, session_policy, agent_id=agent_id)


async def start_conversation(msg: InboundMessage) -> None:
    """Open a new conversation for the chat ``msg`` came from.

    A chat whose conversation holds no message yet (``/new`` twice in a row, or from a chat that never spoke) is already
    a clean start: nothing is opened and the next turn begins there. Otherwise the new conversation is stored now, under
    a key no reset policy derives, so a turn that arrives before or long after this call resolves to it the same way.
    """
    agent_id, session_policy = await _resolve_scope(msg)
    current = await ChatService.get_channel_chat_by_key(await resolve_session_key(msg, session_policy, agent_id=agent_id))
    if current is None or await ChatService.count_messages(current.id) == 0:
        return

    base_key = build_channel_session_key(msg, agent_id=agent_id)
    await ChatService.get_or_create_channel_chat(
        started_session_key(base_key, datetime.now(tz=UTC)),
        msg.channel,
        agent_id=agent_id,
    )
