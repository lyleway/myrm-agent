"""Stands in for everything around the E1 turn body, so a test decides what each step of its setup gives back."""

from __future__ import annotations

from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock

import pytest
from myrm_agent_harness.utils.runtime.cancellation import CancellationRegistry

from app.services.agent.stream_session import (
    chat_history_bootstrap,
    orchestrator_turn_body,
    pre_reply_compact_sse,
)
from app.services.copilot.run_digest_store import RunDigestStore

if TYPE_CHECKING:
    from fastapi import Request
    from myrm_agent_harness.agent.streaming.stream_buffer import GlobalStreamRegistry, ResilientStreamBuffer

    from app.services.agent.params import AgentRequest

CHAT_ID = "chat-1"
MESSAGE_ID = "msg-1"
AGENT_ID = "agent-1"
PERSISTED_MESSAGE_ID = chat_history_bootstrap.BootstrappedMessageId("msg-persisted")


@dataclass(frozen=True)
class MarkerWrite:
    """One write of the interrupted-turn marker: the params it serialised, with the identity they carried at that moment."""

    params: SimpleNamespace
    turn_identity: str | None
    user_message_id: object


@dataclass
class TurnBodyRun:
    """What a run of the turn body was given, and what it handed on."""

    request: AgentRequest
    params: SimpleNamespace
    buffer: SimpleNamespace
    record_failure: AsyncMock
    pre_reply_compact: AsyncMock
    release_unfinished: AsyncMock
    marker_writes: list[MarkerWrite] = field(default_factory=list)
    pumped: list[SimpleNamespace] = field(default_factory=list)
    sessions: list[SimpleNamespace] = field(default_factory=list)


def stub_turn_body(
    monkeypatch: pytest.MonkeyPatch,
    *,
    history_mode: str | None = None,
    build_error: Exception | None = None,
    unavailable_detail: str | None = None,
    marker_error: Exception | None = None,
) -> TurnBodyRun:
    """Replace the steps around the turn body; with no arguments the turn runs through to the pump."""
    params = SimpleNamespace(turn_identity=None)
    turn = SimpleNamespace(
        params=params,
        extra_context={},
        routing_tier=None,
        archive_restore_results=None,
        routing_specialty=None,
        routing_reason=None,
        context_warnings=None,
    )
    request = SimpleNamespace(
        message_id=MESSAGE_ID,
        chat_id=CHAT_ID,
        agent_id=AGENT_ID,
        resume_value=None,
        engine_params=None,
        sibling_group_id=None,
        action_mode="agent",
        workspace_dir=None,
    )
    run = TurnBodyRun(
        request=cast("AgentRequest", request),
        params=params,
        buffer=SimpleNamespace(append=AsyncMock(), end_stream=AsyncMock()),
        record_failure=AsyncMock(),
        pre_reply_compact=AsyncMock(),
        release_unfinished=AsyncMock(),
    )

    async def build_turn(*_args: object, **_kwargs: object) -> SimpleNamespace:
        if build_error is not None:
            raise build_error
        return turn

    async def extra_context(*_args: object, base: dict[str, object] | None = None, **_kwargs: object) -> dict[str, object]:
        return dict(base or {})

    async def write_marker(_request: object, marker_params: SimpleNamespace, *, user_message_id: object = None) -> str:
        run.marker_writes.append(MarkerWrite(marker_params, marker_params.turn_identity, user_message_id))
        if marker_error is not None:
            raise marker_error
        return "marker-1"

    async def pump(session: SimpleNamespace, _buffer: object) -> None:
        run.pumped.append(session)

    def open_session(**kwargs: object) -> SimpleNamespace:
        session = SimpleNamespace(turn_marker_id=None, digest_run=None, **kwargs)
        run.sessions.append(session)
        return session

    monkeypatch.setattr(chat_history_bootstrap, "persist_user_message", AsyncMock(return_value=PERSISTED_MESSAGE_ID))
    monkeypatch.setattr(chat_history_bootstrap, "load_chat_history", AsyncMock(return_value=[]))
    monkeypatch.setattr(orchestrator_turn_body, "resolve_turn_history_mode", AsyncMock(return_value=history_mode))
    monkeypatch.setattr(orchestrator_turn_body, "apply_migration_bound_project", AsyncMock())
    monkeypatch.setattr(orchestrator_turn_body, "build_turn_params", build_turn)
    monkeypatch.setattr(orchestrator_turn_body, "search_service_unavailable_detail", MagicMock(return_value=unavailable_detail))
    monkeypatch.setattr(orchestrator_turn_body, "prepare_goal_provider", AsyncMock(return_value=None))
    monkeypatch.setattr(orchestrator_turn_body, "build_turn_extra_context", extra_context)
    monkeypatch.setattr(orchestrator_turn_body, "write_interrupted_turn_marker", write_marker)
    monkeypatch.setattr(orchestrator_turn_body, "pump_to_buffer", pump)
    monkeypatch.setattr(orchestrator_turn_body, "AgentStreamSession", open_session)
    monkeypatch.setattr(orchestrator_turn_body, "release_unfinished_turn", run.release_unfinished)
    # Process-wide registries: a turn that is only stood in for must not leave an entry for the next test to meet.
    monkeypatch.setattr(orchestrator_turn_body, "SteeringRegistry", MagicMock())
    monkeypatch.setattr(RunDigestStore, "begin_run", classmethod(lambda _cls, _chat_id: None))
    monkeypatch.setattr(pre_reply_compact_sse, "run_pre_reply_compact_with_sse", run.pre_reply_compact)
    return run


async def run_turn_body(run: TurnBodyRun) -> None:
    """Run the turn body to its end, then give back the Stop handle a turn that was pumped still holds."""
    try:
        await orchestrator_turn_body.execute_agent_turn_after_reserve(
            request=run.request,
            http_request=cast("Request", SimpleNamespace()),
            text_content="hello",
            stream_started_at_monotonic=0.0,
            registry=cast("GlobalStreamRegistry", SimpleNamespace()),
            buffer=cast("ResilientStreamBuffer", run.buffer),
            record_terminal_failure=run.record_failure,
        )
    finally:
        CancellationRegistry.unregister(MESSAGE_ID)
