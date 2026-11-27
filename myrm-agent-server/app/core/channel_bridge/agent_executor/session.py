"""Session keys of channel conversations: the peer, thread, agent and reset epoch a chat is stored under.

[INPUT]
- app.channels.types::SessionKey, SessionPolicy, compute_daily_epoch, started_key_time (POS: Session identity, reset policy, epoch computation and the key of a conversation opened on request)
- app.database.repositories.channel_key_match::channel_key_epochs, channel_key_started (POS: Boundary-safe matches of a conversation in ``Chat.channel_session_key``)
- app.services.budget.channel_budget_key::group_budget_key (POS: Canonical key of a group's channel budget)

[OUTPUT]
- resolve_session_key: the key the next turn of a peer is stored under
- build_channel_session_key: the base key of a peer's conversation, without a reset epoch, which a conversation opened on request extends
- build_channel_budget_key: the budget key of a group message

[POS]
The one place that decides which conversation a channel message belongs to. A conversation the person opened on request
(``/new``, ``/handoff``) stays the peer's conversation for as long as the reset policy lets it; otherwise the policy's own
epoch applies.
"""

from __future__ import annotations

from datetime import UTC, datetime

from app.channels.types import (
    InboundMessage,
    SessionKey,
    SessionPolicy,
    SessionResetMode,
    compute_daily_epoch,
    started_key_time,
)
from app.services.budget.channel_budget_key import group_budget_key


def _resolve_peer(msg: InboundMessage) -> tuple[str, str]:
    """Derive (peer_kind, peer_id) from an inbound message.

    Single source of truth for the peer of an inbound message: the session key and the channel budget key are both
    built from it. Groups use chat_id; DMs use sender_id.
    """
    peer_kind = "group" if msg.is_group else "dm"
    peer_id = msg.chat_id if msg.is_group and msg.chat_id else msg.sender_id
    if not peer_id:
        peer_id = f"channel-{msg.channel}"
    return peer_kind, peer_id


def build_channel_budget_key(msg: InboundMessage) -> str:
    """Build the channel budget key of a group message for the budget guard lookup.

    Returns empty string for non-group messages (DM / WebUI). The key is the session key of the group without epoch,
    topic or agent (``group_budget_key``), so it prefixes the session key of every conversation of the group.
    """
    if not msg.is_group:
        return ""
    _, peer_id = _resolve_peer(msg)
    return group_budget_key(msg.channel, peer_id)


def build_channel_session_key(
    msg: InboundMessage,
    *,
    agent_id: str | None = None,
) -> str:
    """Return the base ``channel_session_key`` for a message's conversation, without a reset epoch.

    Public counterpart of ``_build_session_key`` for callers that derive a key of their own from the base: a
    conversation opened on request extends it with its opening time (``started_session_key``).
    """
    return _build_session_key(msg, agent_id=agent_id)


def _build_session_key(
    msg: InboundMessage,
    *,
    agent_id: str | None = None,
) -> str:
    """Build a structured session key (base, without epoch).

    Dimensions: channel + peer_kind + peer + thread + agent. Groups use chat_id and DMs use sender_id, so the
    members of a group (or of a thread in it) share one conversation and each DM sender has their own.
    """
    peer_kind, peer_id = _resolve_peer(msg)

    sk = SessionKey(
        channel=msg.channel,
        peer_kind=peer_kind,
        peer_id=peer_id,
        thread_id=msg.thread_id,
        agent_id=agent_id,
    )
    return str(sk.to_str())


async def resolve_session_key(
    msg: InboundMessage,
    policy: SessionPolicy,
    *,
    agent_id: str | None = None,
) -> str:
    """Resolve the final session key according to the reset policy.

    - opened on request (``/new``, ``/handoff``): the newest such conversation, while the policy lets it continue
    - persistent: base key only (one Chat per peer forever)
    - daily: base key + date epoch (new Chat each day at reset_hour UTC)
    - idle: base key + time epoch (new Chat after idle_minutes of inactivity)
    """
    base = _build_session_key(msg, agent_id=agent_id)

    started_key = await _current_started_key(base, policy)
    if started_key is not None:
        return started_key

    match policy.mode:
        case SessionResetMode.PERSISTENT:
            return base
        case SessionResetMode.DAILY:
            epoch = compute_daily_epoch(policy.daily_reset_hour)
            return f"{base}:e={epoch}"
        case SessionResetMode.IDLE:
            return await _resolve_idle_key(base, policy.idle_minutes)
    return base


async def _current_started_key(base_key: str, policy: SessionPolicy) -> str | None:
    """The key of the newest conversation the person opened on request, while it is still the one they talk in.

    A conversation opened on request is not tied to the reset epoch the policy derives, so the policy only decides how
    long it lasts: persistent, until another is opened; daily, until the next reset hour; idle, until it has been idle for
    ``idle_minutes``. Its own last activity is what counts, so a late write to an older conversation does not bring that
    one back. ``None`` when the peer opened none or it has ended: the policy's own key applies then.
    """
    from sqlalchemy import select

    from app.database.connection import get_session
    from app.database.models import Chat
    from app.database.repositories.channel_key_match import channel_key_started

    async with get_session() as session:
        result = await session.execute(
            select(Chat.channel_session_key, Chat.updated_at)
            .where(channel_key_started(base_key))
            .order_by(Chat.channel_session_key.desc())
            .limit(1)
        )
        row = result.first()
    if row is None or row[0] is None:
        return None

    key: str = row[0]
    updated_at: datetime = row[1]
    now = datetime.now(tz=UTC)
    match policy.mode:
        case SessionResetMode.PERSISTENT:
            return key
        case SessionResetMode.DAILY:
            opened_at = started_key_time(key)
            same_epoch = opened_at is not None and compute_daily_epoch(
                policy.daily_reset_hour, at=opened_at
            ) == compute_daily_epoch(policy.daily_reset_hour, at=now)
            return key if same_epoch else None
        case SessionResetMode.IDLE:
            return None if _is_idle_expired(updated_at, now, policy.idle_minutes) else key
    return None


def _is_idle_expired(updated_at: datetime, now: datetime, idle_minutes: int) -> bool:
    """Whether a chat last active at ``updated_at`` (a naive time is UTC) has been idle for more than ``idle_minutes``."""
    if updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=UTC)
    return (now - updated_at).total_seconds() > idle_minutes * 60


async def _resolve_idle_key(
    base_key: str,
    idle_minutes: int,
) -> str:
    """Resolve session key for idle mode by checking the latest Chat's activity.

    Only the conversation's own chats count (its key, its reset epochs and the conversations opened on request):
    the chat of another peer, topic or agent that shares the key's leading characters must not be continued.
    """
    from sqlalchemy import select

    from app.database.connection import get_session
    from app.database.models import Chat
    from app.database.repositories.channel_key_match import channel_key_epochs

    async with get_session() as session:
        result = await session.execute(
            select(Chat.updated_at, Chat.channel_session_key)
            .where(channel_key_epochs(base_key))
            .order_by(Chat.updated_at.desc())
            .limit(1)
        )
        row = result.first()

    now = datetime.now(tz=UTC)
    new_epoch_key = f"{base_key}:e={now.strftime('%Y%m%dT%H%M')}"

    if row is None or row[1] is None:
        return new_epoch_key

    updated_at: datetime = row[0]
    existing_key: str = row[1]

    if _is_idle_expired(updated_at, now, idle_minutes):
        return new_epoch_key

    return existing_key
