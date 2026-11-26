"""The conversation an IM command acts on: the one the next turn of the sending chat is stored in.

[INPUT]
- app.channels.routing.topic_binding::resolve_bound_topic (POS: The one cascade that picks the binding serving a message)
- app.channels.types::InboundMessage
- app.core.channel_bridge.agent_executor::resolve_session_key (POS: Session key from reset policy, agent and epoch)
- app.core.channel_bridge.config_loader::load_user_configs
- app.core.channel_bridge.config_parsers::extract_session_policy, session_policy_from_agent_dict
- app.core.channel_bridge.topic_config::SqlTopicManager
- app.services.agent.profile.profile_resolver::get_agent_profile_resolver

[OUTPUT]
- resolve_command_session_key: session key a message's conversation is stored under, or None while ``/new`` has ended it and no turn has begun the next

[POS]
/undo, /retry, /compact, /handoff and /goal act on the conversation of the chat they are sent from. The agent that
serves the message and the reset policy that applies decide which conversation that is, so this resolves both the way
the turn executor does (the serving binding's ``effective_agent_id``; the agent's own policy, else the person's). A
command that resolved either differently would act on another conversation than the one the user is talking in. After
``/new`` the next turn opens a new conversation, so until it does there is none for a command to act on.
"""

from __future__ import annotations

from app.channels.routing.topic_binding import resolve_bound_topic
from app.channels.types import InboundMessage
from app.core.channel_bridge.agent_executor import resolve_session_key
from app.core.channel_bridge.config_loader import load_user_configs
from app.core.channel_bridge.config_parsers import extract_session_policy, session_policy_from_agent_dict
from app.core.channel_bridge.topic_config import SqlTopicManager
from app.services.agent.profile.profile_resolver import get_agent_profile_resolver


async def resolve_command_session_key(msg: InboundMessage) -> str | None:
    """The session key under which the turns of ``msg``'s conversation are stored right now.

    ``None`` when ``msg`` carries ``force_new_epoch``: ``/new`` ended that conversation and the next turn starts another.
    """
    if msg.metadata.get("force_new_epoch"):
        return None

    topic = await resolve_bound_topic(SqlTopicManager(), msg)
    agent_id = topic.effective_agent_id if topic else None

    configs = await load_user_configs()
    session_policy = extract_session_policy(configs.personal_settings_dict)
    if agent_id:
        profile = await get_agent_profile_resolver().resolve(agent_id)
        if profile and profile.session_policy:
            session_policy = session_policy_from_agent_dict(profile.session_policy)

    return await resolve_session_key(msg, session_policy, agent_id=agent_id)
