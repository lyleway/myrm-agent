"""Shared fixtures for the inbound-dispatch step tests: a message builder, a router whose handlers are recorders, and
the helpers that read what a dispatch published or handed to the session gate."""

from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock, MagicMock

from myrm_agent_harness.utils.runtime.cancellation import CancellationToken
from myrm_agent_harness.utils.runtime.steering import SteeringToken

from app.channels.routing.router import AgentRouter
from app.channels.routing.router_keys import routing_state_key
from app.channels.routing.router_models import _ActiveTask
from app.channels.types import InboundMessage, OutboundMessage

from ..risk_gate_testkit import make_router

PIPELINE = "app.channels.routing.pipeline.inbound"


def inbound_msg(
    content: str,
    *,
    message_id: str = "m1",
    is_group: bool = False,
    thread_id: str | None = None,
    user_id: str | None = None,
    sender_name: str = "",
    metadata: dict[str, object] | None = None,
) -> InboundMessage:
    return InboundMessage(
        channel="telegram",
        sender_id="u1",
        chat_id="chat-1",
        content=content,
        message_id=message_id,
        is_group=is_group,
        thread_id=thread_id,
        user_id=user_id,
        sender_name=sender_name,
        metadata=metadata or {},
    )


def dispatch_router() -> AgentRouter:
    """A router whose sender checks are decided, whose gate is a recorder, and whose handlers record their calls."""
    router = make_router()
    recorders = {
        "_is_sender_admitted": AsyncMock(return_value=False),
        "_check_admin_permission": AsyncMock(return_value=False),
        "_handle_action_button_approval": AsyncMock(),
        "_handle_approval_command": AsyncMock(),
        "_dispatch_resolved": AsyncMock(return_value=True),
        "_handle_bang_command": AsyncMock(),
        "_handle_delegation_inbound": AsyncMock(return_value=True),
    }
    for name, recorder in recorders.items():
        setattr(router, name, recorder)
    router._gate = MagicMock()
    router._delegation_coordinator = MagicMock()
    router._delegation_coordinator.find_active_task.return_value = None
    return router


def running_turn(router: AgentRouter, *, thread_id: str | None = None, requester_id: str = "") -> SteeringToken:
    token = SteeringToken()
    task = asyncio.current_task()
    assert task is not None
    router._active_tasks[routing_state_key("telegram", "chat-1", thread_id)] = _ActiveTask(
        task=task,
        cancel_token=CancellationToken(),
        channel="telegram",
        chat_id="chat-1",
        placeholder_id=None,
        started_at=0.0,
        steering_token=token,
        requester_id=requester_id,
    )
    return token


def command_dispatch(router: AgentRouter) -> AsyncMock:
    """The recorder that stands in for the command dispatch of ``dispatch_router``."""
    return cast(AsyncMock, router._dispatch_resolved)


def published_messages(router: AgentRouter) -> list[OutboundMessage]:
    publish = cast(AsyncMock, router._bus.publish_outbound)
    return [call.args[0] for call in publish.await_args_list]


def gate_submissions(router: AgentRouter) -> list[InboundMessage]:
    submit = cast(MagicMock, router._gate.submit)
    return [call.args[0] for call in submit.call_args_list]


async def run_dispatch(router: AgentRouter, msg: InboundMessage) -> None:
    await router._dispatch_inbound_message(msg)
    await asyncio.sleep(0)  # a step that hands off to a task runs it before the assertions
