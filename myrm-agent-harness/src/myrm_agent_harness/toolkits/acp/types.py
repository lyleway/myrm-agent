"""Core type definitions for the ACP runtime system.

Defines the fundamental abstractions: RuntimeBackend Protocol, RuntimeEvent,
error codes, configuration, and the permission vocabulary. All other ACP runtime modules
depend on this module.


[INPUT]
no - Base type definition module

[OUTPUT]
- RuntimeBackend: Runtime Backend abstract protocol
- DelegationPool: the pool surface a delegation caller needs (RuntimePool and host-side wrappers satisfy it)
- RuntimeEvent: Runtime Event type
- AcpErrorCode: ACP error enum
- ContinuityStatus: status_update statuses for an agent conversation that could not be continued
- StopReason: how an agent ended a turn (the ``stop_reason`` of ``done``), in the vocabulary of ACP, and the notice each unfinished ending earns
- IMAGES_NOT_SENT_STATUS, images_not_sent_event: the status_update telling that a turn's images were not handed to the agent
- BackendType, RESIDENT_BACKEND_TYPES: how a backend is run (ACP, a CLI process per turn, Codex's app server) and which of those keep one resident process
- RuntimeConfig: Runtime configuration (the working directory is not part of it: a delegated turn names its workspace; the home of a named account is)
- normalize_workspace: the one check a workspace goes through before a turn, runtime or pool uses it
- delegation_session_id: the one name of an agent's persistent conversation in a scope, whichever entry the turn comes through
- CliSessionRecord: an agent-native session id bound to the working directory that created it
- CliSessionStore: protocol a host implements to keep agent session records across process restarts
- PermissionMode: what a delegated agent may do without asking (``safe`` or ``allow_all``)
- PermissionRequest, PermissionVerdict, PermissionBroker: one action a ``safe`` agent asks to take, the answer, and the host-side party that puts it to a person

[POS]
ACP runtime type definitions layer. Provides all ACP-related core abstractions and data
structures, serving as the foundation for the entire ACP module.
"""

from __future__ import annotations

import os
import time
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal, Protocol, runtime_checkable

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class RuntimeEventType(StrEnum):
    """Event types emitted during a runtime turn."""

    TEXT_DELTA = "text_delta"
    REASONING_DELTA = "reasoning_delta"
    TOOL_START = "tool_start"
    TOOL_RESULT = "tool_result"
    USAGE_UPDATE = "usage_update"
    STATUS_UPDATE = "status_update"
    ERROR = "error"
    DONE = "done"


class AcpErrorCode(StrEnum):
    """Structured error codes for ACP runtime operations."""

    AUTH_FAILED = "auth_failed"
    BACKEND_NOT_FOUND = "backend_not_found"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    PROCESS_CRASHED = "process_crashed"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


class ContinuityStatus(StrEnum):
    """``status_update`` statuses telling that the conversation an agent recorded earlier is not continued."""

    SESSION_EXPIRED = "session_expired"
    WORKSPACE_CHANGED = "session_workspace_changed"

    @classmethod
    def of(cls, status: object) -> ContinuityStatus | None:
        """The continuity status that ``status`` stands for, or None for any other status."""
        if not isinstance(status, str):
            return None
        try:
            return cls(status)
        except ValueError:
            return None


class StopReason(StrEnum):
    """How an agent ended a turn: the ``stop_reason`` of ``done``, in the vocabulary of ACP's ``PromptResponse``.

    ``end_turn`` is the normal end. The others tell the person that the reply is not what a finished turn leaves:
    ``max_tokens`` cut it off, ``max_turn_requests`` stopped it at the agent's own limit, ``refusal`` declined it (and
    the agent dropped the prompt from its context); ``cancelled`` is the host's own stop.
    """

    END_TURN = "end_turn"
    MAX_TOKENS = "max_tokens"
    MAX_TURN_REQUESTS = "max_turn_requests"
    REFUSAL = "refusal"
    CANCELLED = "cancelled"

    @classmethod
    def of(cls, stop_reason: object) -> StopReason | None:
        """The stop reason that ``stop_reason`` stands for, or None for any other value."""
        if not isinstance(stop_reason, str):
            return None
        try:
            return cls(stop_reason)
        except ValueError:
            return None

    @property
    def notice(self) -> str | None:
        """The kind of notice (``delegation_<agent>_<kind>``) a person is shown for a turn that ended this way.

        None when there is nothing to say: the turn finished, or the person stopped it.
        """
        return _STOP_NOTICES.get(self)


_STOP_NOTICES: dict[StopReason, str] = {
    StopReason.MAX_TOKENS: "max_tokens",
    StopReason.MAX_TURN_REQUESTS: "turn_limit",
    StopReason.REFUSAL: "refusal",
}

# The ``status_update`` status of a turn whose images were not handed to the agent (its backend or the agent itself
# takes none); the agent still has the image's location in the prompt.
IMAGES_NOT_SENT_STATUS = "images_not_sent"

# ``safe``: the agent reads freely and asks before anything else (an edit, a command, a download); where nobody can
# answer it, it may only read. ``allow_all``: the agent acts without asking.
PermissionMode = Literal["safe", "allow_all"]

# How a delegated backend is run: over the Agent Client Protocol, as a CLI process per turn, or over Codex's own app
# server. The ACP and app-server backends keep one resident process that serves many turns.
BackendType = Literal["acp", "cli", "app_server"]
RESIDENT_BACKEND_TYPES: frozenset[BackendType] = frozenset({"acp", "app_server"})

BackendStatus = Literal["ready", "starting", "error", "stopped"]

# How a delegated backend authenticates with its model provider.
# - "subscription": the backend uses its own logged-in credentials (the user's
#   ChatGPT Plus / Claude Max / Gemini subscription persisted by the CLI itself);
#   provider API keys are stripped from the child environment so the CLI is forced
#   onto its subscription session and never silently falls back to metered API billing.
# - "api_key": the host injects a specific provider key via ``RuntimeConfig.env``;
#   usage is billed per token against that key.
AuthMode = Literal["subscription", "api_key"]


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class AcpError:
    """Structured error with code, message, and retryable flag."""

    code: AcpErrorCode
    message: str
    retryable: bool = False
    details: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class RuntimeEvent:
    """A single event emitted during a runtime turn."""

    type: RuntimeEventType
    data: dict[str, object]
    session_id: str
    timestamp: float = field(default_factory=time.time)


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """Declares what a RuntimeBackend supports."""

    supports_resume: bool = False
    supports_mcp: bool = False
    supports_streaming: bool = True
    supports_tools: bool = True
    # Image files can be attached to a turn (``run_turn(images=...)``); a backend without it is told nothing about them.
    supports_images: bool = False
    # In ``safe`` mode the backend puts what it may not do on its own to the turn's ``PermissionBroker``; a backend
    # without it can only refuse, so there ``safe`` means read-only.
    supports_permission_requests: bool = False


@dataclass(frozen=True, slots=True)
class BackendInfo:
    """Runtime-queryable metadata about a backend."""

    name: str
    version: str | None = None
    backend_type: BackendType = "acp"
    status: BackendStatus = "ready"
    capabilities: BackendCapabilities = field(default_factory=BackendCapabilities)
    metadata: dict[str, object] | None = None


@dataclass(frozen=True, slots=True)
class McpServerConfig:
    """Configuration for an MCP server to inject into a runtime."""

    name: str
    command: str
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None


@dataclass(frozen=True, slots=True)
class RuntimeConfig:
    """Unified configuration for any RuntimeBackend."""

    backend_type: BackendType
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] | None = None
    timeout_seconds: int = 300
    permission_mode: PermissionMode = "safe"
    auth_mode: AuthMode = "subscription"
    mcp_servers: list[McpServerConfig] = field(default_factory=list)
    max_response_chars: int = 50_000
    max_turns: int = 25
    description: str = ""
    # The directory of a named account (``auth.AccountStore``) the agent's CLI keeps its state and its login in,
    # absolute, for a known agent only. The CLI is relocated into it whatever ``env`` says; ``None``: the CLI's
    # own default home.
    account_home: str | None = None


@dataclass(frozen=True, slots=True)
class CliSessionRecord:
    """An agent-native session id (a CLI's, an ACP agent's, a Codex thread's) bound to the directory that created it.

    Claude Code files a conversation under its project directory, and before v2.1.223 ``--resume <id>`` finds
    it only from that directory. A turn resumes a record only when it starts in the directory that created
    it, which holds for every version.
    """

    cli_session_id: str
    cwd: str


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """One action a ``safe`` agent asks to take, as much of it as the agent says."""

    agent: str
    session_id: str
    tool_name: str
    # The agent's own kind of tool: ``read``, ``search``, ``edit``, ``delete``, ``move``, ``execute``, ``fetch``, ``think``
    # or ``other`` (what an agent does not classify).
    kind: str
    title: str
    # What the agent is about to do (the command, the file); loosely typed because each agent words it its own way.
    input: dict[str, object]
    paths: tuple[str, ...] = ()
    tool_call_id: str | None = None


@dataclass(frozen=True, slots=True)
class PermissionVerdict:
    """A person's answer to a ``PermissionRequest``; ``message`` is why it was refused, for agents that can be told."""

    allowed: bool
    message: str | None = None


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class PermissionBroker(Protocol):
    """Puts one permission request of a delegated agent to a person and returns what they decided.

    A host implements it and hands it to a turn. ``decide`` waits as long as the person takes (the turn's own time
    limit stands still meanwhile) and ends the wait itself, with a refusal, when it gives up. A turn without a broker
    has nobody to ask, so a ``safe`` agent there may only read; a ``decide`` that raises refuses the request.
    """

    async def decide(self, request: PermissionRequest) -> PermissionVerdict: ...


@runtime_checkable
class RuntimeBackend(Protocol):
    """Unified interface for ACP and CLI agent backends.

    Implementations must support the full lifecycle: run_turn, cancel, resume,
    get_info, and close.
    """

    @property
    def name(self) -> str: ...

    @property
    def capabilities(self) -> BackendCapabilities: ...

    @property
    def is_alive(self) -> bool: ...

    def run_turn(
        self,
        prompt: str,
        session_id: str,
        *,
        mcp_servers: list[McpServerConfig] | None = None,
        images: list[str] | None = None,
        permissions: PermissionBroker | None = None,
    ) -> AsyncGenerator[RuntimeEvent]: ...

    async def cancel(self, session_id: str) -> None: ...

    async def resume(self, session_id: str) -> bool: ...

    async def get_info(self) -> BackendInfo: ...

    async def close(self) -> None: ...


class DelegationPool(Protocol):
    """What a caller needs to delegate turns to configured backends.

    ``RuntimePool`` satisfies it, and so can a host-side wrapper that adds concerns of its own
    (per-conversation turn serialization, pool lifetime) without subclassing the pool.
    """

    @property
    def available_backends(self) -> list[str]: ...

    def get_config(self, name: str) -> RuntimeConfig | None: ...

    def run_turn(
        self,
        name: str,
        prompt: str,
        session_id: str,
        *,
        workspace: str,
        mode: str = "persistent",
        mcp_servers: list[McpServerConfig] | None = None,
        images: list[str] | None = None,
        permissions: PermissionBroker | None = None,
    ) -> AsyncGenerator[RuntimeEvent]: ...

    async def cancel(self, name: str, session_id: str) -> None: ...


class CliSessionStore(Protocol):
    """Durable home of ``CliSessionRecord`` keyed by harness session id; one store serves one conversation scope.

    A host implements it so agent sessions survive a restart of the host process or of the agent. The runtime awaits
    every call while a turn runs (a record is saved as soon as the agent announces its session), so an implementation
    bounds its own latency; a call that raises is degraded by the runtime to a fresh agent session and never fails a
    turn. A record is never deleted: the next session the agent announces overwrites it.
    """

    async def load(self, session_id: str) -> CliSessionRecord | None: ...

    async def save(self, session_id: str, record: CliSessionRecord) -> None: ...


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def normalize_workspace(workspace: str) -> str:
    """The directory a delegated turn starts in, as a normalized absolute path.

    Raises:
        ValueError: ``workspace`` is not an absolute path. A relative one would be resolved against whatever
            directory the host process happens to run in, the ambient inference a turn must not depend on.
    """
    if not os.path.isabs(workspace):
        msg = f"workspace must be an absolute path, got {workspace!r}"
        raise ValueError(msg)
    return os.path.normpath(workspace)


def delegation_session_id(agent_name: str, session_scope: str | None = None) -> str:
    """The session id of the persistent conversation with ``agent_name`` in one scope (a chat).

    Every entry into the same agent in the same scope names this session, so a conversation that moves between
    entries continues one external session instead of opening a second.
    """
    return f"{agent_name}-{session_scope or 'default'}"


def create_event(
    event_type: RuntimeEventType,
    session_id: str,
    **data: object,
) -> RuntimeEvent:
    """Convenience factory for creating RuntimeEvent instances."""
    return RuntimeEvent(type=event_type, data=data, session_id=session_id)


def images_not_sent_event(session_id: str, images: int) -> RuntimeEvent:
    """The notice that the ``images`` image files of a turn were not handed to the agent."""
    return create_event(RuntimeEventType.STATUS_UPDATE, session_id, status=IMAGES_NOT_SENT_STATUS, images=images)
