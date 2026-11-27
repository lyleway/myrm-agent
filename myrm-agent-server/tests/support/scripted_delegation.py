"""A scripted external-agent pool for tests of how a delegated turn's events reach the frontend.

``ScriptedPool`` answers ``run_turn`` with a canned event sequence and records how ``stream_direct_delegate`` drove
it (turns started, the workspaces they ran in, cancels issued); the small helpers build events and read the frontend
events it produced.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator, Callable

from myrm_agent_harness.api import AgentEventType
from myrm_agent_harness.toolkits.acp.types import (
    AcpError,
    AcpErrorCode,
    AuthMode,
    PermissionBroker,
    RuntimeConfig,
    RuntimeEvent,
    RuntimeEventType,
    create_event,
)
from myrm_agent_harness.utils import CancellationToken

from app.services.external_agents.delegation.stream import SseEvent, stream_direct_delegate

STEPS = AgentEventType.TASKS_STEPS.value
CANCELLED = AgentEventType.CANCELLED.value
MESSAGE_END = AgentEventType.MESSAGE_END.value
WORKSPACE = "/work/repo"


TurnScript = Callable[["ScriptedPool"], AsyncGenerator[RuntimeEvent]]


class ScriptedPool:
    """A ``DelegationPool`` whose turn plays a script and records how the stream drove it."""

    def __init__(self, turn: TurnScript, *, auth_mode: AuthMode | None = "subscription") -> None:
        self._turn = turn
        self._config = RuntimeConfig(backend_type="cli", auth_mode=auth_mode) if auth_mode else None
        self.turns: list[tuple[str, str, str]] = []
        self.turn_workspaces: list[str] = []
        self.turn_images: list[list[str] | None] = []
        self.turn_permissions: list[PermissionBroker | None] = []
        self.cancelled: list[tuple[str, str]] = []
        self.cancel_called = asyncio.Event()

    @property
    def available_backends(self) -> list[str]:
        return ["claude"]

    def get_config(self, name: str) -> RuntimeConfig | None:
        return self._config

    def run_turn(
        self,
        name: str,
        prompt: str,
        session_id: str,
        *,
        workspace: str,
        mode: str = "persistent",
        mcp_servers: object = None,
        images: list[str] | None = None,
        permissions: PermissionBroker | None = None,
    ) -> AsyncGenerator[RuntimeEvent]:
        self.turns.append((name, prompt, session_id))
        self.turn_workspaces.append(workspace)
        self.turn_images.append(images)
        self.turn_permissions.append(permissions)
        return self._turn(self)

    async def cancel(self, name: str, session_id: str) -> None:
        self.cancelled.append((name, session_id))
        self.cancel_called.set()


def scripted_event(event_type: RuntimeEventType, **data: object) -> RuntimeEvent:
    return create_event(event_type, "claude-chat-1", **data)


def scripted_error(code: AcpErrorCode, message: str = "it broke") -> RuntimeEvent:
    return scripted_event(RuntimeEventType.ERROR, error=AcpError(code=code, message=message))


def playing(*events: RuntimeEvent) -> TurnScript:
    async def turn(_pool: ScriptedPool) -> AsyncGenerator[RuntimeEvent]:
        for event in events:
            yield event

    return turn


async def stream_turn(
    pool: ScriptedPool,
    token: CancellationToken | None = None,
    *,
    images: list[str] | None = None,
    permissions: PermissionBroker | None = None,
) -> list[SseEvent]:
    return [
        event
        async for event in stream_direct_delegate(
            pool,
            "claude",
            "do it",
            session_id="claude-chat-1",
            workspace=WORKSPACE,
            images=images,
            permissions=permissions,
            cancel_token=token,
        )
    ]


def event_types(events: list[SseEvent]) -> list[object]:
    return [event["type"] for event in events]


def steps_of(events: list[SseEvent], step_key: str) -> list[SseEvent]:
    return [event for event in events if event["type"] == STEPS and event["step_key"] == step_key]


def step_text(step: SseEvent) -> object:
    data = step["data"]
    assert isinstance(data, list) and len(data) == 1
    return data[0]["text"]
