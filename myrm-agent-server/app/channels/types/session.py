"""Session identity, reset policy and daily epoch computation.

[INPUT]
(No external dependencies, pure data types and time computation)

[OUTPUT]
- SessionKey: Session identifier
- SessionResetMode, SessionPolicy: Session reset strategy
- METADATA_NEW_CONVERSATION_KEY: Inbound metadata flag that asks for a new conversation
- METADATA_STORED_CHAT_KEY: Outbound metadata naming the stored chat a reply was produced in
- compute_daily_epoch(): Daily-granularity time epoch computation, for now or for a given moment
- started_session_key(), started_key_time(), is_started_session_key(): the key of a conversation a person opened on
  request, and how to recognise one

[POS]
Session identity and policy definitions. Provides session-isolated key generation
and time/idle-based auto-reset policies.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

_SAFE_CHARS = re.compile(r"[^a-zA-Z0-9_-]")

_STARTED_STAMP_FORMAT = "%Y%m%dT%H%M%S"
_STARTED_KEY = re.compile(r":e=s(\d{8}T\d{6})$")

# Inbound metadata flag a forwarder sets on the first message after a ``/new`` it answered itself.
METADATA_NEW_CONVERSATION_KEY = "force_new_epoch"

# Outbound metadata the producer of a reply sets to the stored chat the reply was produced in (the database chat id): the
# router links that chat, which alias routing, a resumed approval and the reset policy decide for each turn.
METADATA_STORED_CHAT_KEY = "stored_chat_id"


@dataclass(frozen=True, slots=True)
class SessionKey:
    """Structured session key.

    Encodes channel, peer, topic and agent into a deterministic string that serves as
    the conversation history DB key. The peer of a group is its chat and the peer of a
    DM is its sender; there is no tenant dimension. Structured fields allow callers to
    inspect individual dimensions without parsing.

    Format: ``{channel}:{peer_kind}:{peer}[:thread:{thread}][:agent:{agent}]``
    """

    channel: str
    peer_kind: str
    peer_id: str
    thread_id: str | None = None
    agent_id: str | None = None

    def to_str(self) -> str:
        sanitize = _SAFE_CHARS.sub
        parts = [
            sanitize("_", self.channel),
            self.peer_kind,
            sanitize("_", self.peer_id),
        ]
        if self.thread_id:
            parts.append(f"thread:{sanitize('_', self.thread_id)}")
        if self.agent_id:
            parts.append(f"agent:{sanitize('_', self.agent_id)}")
        return ":".join(parts).lower()

    @staticmethod
    def parse(raw: str) -> SessionKey | None:
        """Best-effort parse of a serialised session key."""
        parts = raw.split(":")
        if len(parts) < 3:
            return None
        thread_id: str | None = None
        agent_id: str | None = None
        idx = 3
        while idx < len(parts) - 1:
            tag = parts[idx]
            val = parts[idx + 1]
            if tag == "thread":
                thread_id = val
            elif tag == "agent":
                agent_id = val
            idx += 2
        return SessionKey(
            channel=parts[0],
            peer_kind=parts[1],
            peer_id=parts[2],
            thread_id=thread_id,
            agent_id=agent_id,
        )


class SessionResetMode(StrEnum):
    """How an IM channel session is segmented into separate Chat records."""

    PERSISTENT = "persistent"
    DAILY = "daily"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class SessionPolicy:
    """Declarative session reset policy for IM channels.

    Controls when a new Chat record is created for the same IM peer.
    - persistent: one Chat per peer forever (legacy default)
    - daily: new Chat after ``daily_reset_hour`` UTC each day
    - idle: new Chat after ``idle_minutes`` of inactivity

    ``notify_on_reset``: when True, the executor injects a system context note
    into the LLM query and emits a user-facing IM notification on auto-reset.
    """

    mode: SessionResetMode = SessionResetMode.DAILY
    daily_reset_hour: int = 4
    idle_minutes: int = 120
    notify_on_reset: bool = True


def compute_daily_epoch(reset_hour: int, *, at: datetime | None = None) -> str:
    """Pure-computation epoch for daily reset (zero DB cost).

    Returns an ISO date string that stays the same from *reset_hour* until *reset_hour* the next day, for the UTC
    moment *at* (default: now). Messages within the same epoch share a Chat.
    """
    moment = at or datetime.now(tz=UTC)
    cutoff = moment.replace(hour=reset_hour, minute=0, second=0, microsecond=0)
    if moment < cutoff:
        cutoff -= timedelta(days=1)
    return cutoff.date().isoformat()


def started_session_key(base_key: str, at: datetime) -> str:
    """The key of a conversation a person opened on request (``/new``, ``/handoff``) at the aware moment *at*.

    The ``s`` before the stamp keeps these keys apart from the epoch keys a reset policy derives (those begin with a
    digit), so every conversation opened on request lies in the key range ``[{base}:e=s, {base}:e=t)`` and the newest is
    the last key of that range, whatever the policy.
    """
    return f"{base_key}:e=s{at.astimezone(UTC).strftime(_STARTED_STAMP_FORMAT)}"


def started_key_time(key: str) -> datetime | None:
    """When the conversation stored under *key* was opened on request; ``None`` for any other key."""
    found = _STARTED_KEY.search(key)
    if found is None:
        return None
    try:
        return datetime.strptime(found.group(1), _STARTED_STAMP_FORMAT).replace(tzinfo=UTC)
    except ValueError:
        return None


def is_started_session_key(key: str) -> bool:
    """Whether *key* is the key of a conversation a person opened on request."""
    return started_key_time(key) is not None
