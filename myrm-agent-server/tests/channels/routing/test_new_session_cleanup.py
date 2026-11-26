"""Tests for /new session boundary cleanup (IR-03).

Validates that ``_handle_new_session`` cancels the running task, flushes pending messages and resets per-session
state, and only then opens the new conversation through ``start_conversation``.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.channels.i18n import get_text
from app.channels.routing.commands import RouterCommandsMixin
from app.channels.routing.pending_approvals import PendingApprovals
from app.channels.routing.router_models import _ActiveTask
from app.channels.types import InboundMessage
from tests.support.pending_approvals import empty_registry, waiting

if TYPE_CHECKING:
    from app.channels.routing.router_host import RouterCommandsHost


def _msg(
    content: str = "/new",
    *,
    channel: str = "telegram",
    sender_id: str = "user1",
    chat_id: str | None = None,
    is_group: bool = False,
) -> InboundMessage:
    return InboundMessage(
        channel=channel,
        sender_id=sender_id,
        content=content,
        chat_id=chat_id,
        is_group=is_group,
    )


def _make_active_task(
    *,
    channel: str = "telegram",
    chat_id: str = "user1",
    placeholder_id: str | None = None,
    done: bool = False,
) -> _ActiveTask:
    task = MagicMock(spec=asyncio.Task)
    task.done.return_value = done
    cancel_token = MagicMock()
    return _ActiveTask(
        task=task,
        cancel_token=cancel_token,
        channel=channel,
        chat_id=chat_id,
        placeholder_id=placeholder_id,
        started_at=0.0,
        requester_id="user1",
    )


def _mock_of(double: object) -> MagicMock:
    """A field of an ``_ActiveTask`` that ``_make_active_task`` filled with a mock, as the mock it is."""
    return cast(MagicMock, double)


@dataclass(slots=True)
class _Host(RouterCommandsMixin):
    _active_tasks: dict[str, _ActiveTask] = field(default_factory=dict)
    _pending_approvals: PendingApprovals = field(default_factory=empty_registry)
    _bus: MagicMock = field(default_factory=MagicMock)
    _fx: MagicMock = field(default_factory=MagicMock)
    _gate: MagicMock = field(default_factory=MagicMock)
    _session_yolo: dict[str, tuple[float, int | None]] = field(default_factory=dict)
    _session_personality: dict[str, str] = field(default_factory=dict)


def _host(
    *,
    active_tasks: dict[str, _ActiveTask] | None = None,
    pending_approvals: PendingApprovals | None = None,
    session_yolo: dict[str, tuple[float, int | None]] | None = None,
    session_personality: dict[str, str] | None = None,
) -> _Host:
    bus = MagicMock()
    bus.publish_outbound = AsyncMock()
    fx = MagicMock()
    fx.cleanup_placeholder = AsyncMock()
    gate = MagicMock()
    gate.clear_pending_for_key = MagicMock(return_value=0)
    return _Host(
        _active_tasks=active_tasks if active_tasks is not None else {},
        _pending_approvals=pending_approvals if pending_approvals is not None else empty_registry(),
        _bus=bus,
        _fx=fx,
        _gate=gate,
        _session_yolo=session_yolo if session_yolo is not None else {},
        _session_personality=session_personality if session_personality is not None else {},
    )


def _as_router(host: _Host) -> RouterCommandsHost:
    """The host as the mixin methods see it: they read only the attributes ``_Host`` supplies."""
    return cast("RouterCommandsHost", host)


async def _handle_new_session(host: _Host, msg: InboundMessage) -> None:
    await RouterCommandsMixin._handle_new_session(_as_router(host), msg)


class TestAbortSessionTask:
    """Unit tests for _abort_session_task (shared by /stop and /new)."""

    @pytest.mark.asyncio
    async def test_returns_false_when_no_active_task(self) -> None:
        h = _host()
        result = await _as_router(h)._abort_session_task("telegram:user1", reason="test")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancels_token_and_task(self) -> None:
        active = _make_active_task()
        h = _host(active_tasks={"telegram:user1": active})

        result = await _as_router(h)._abort_session_task("telegram:user1", reason="User /stop command")

        assert result is True
        _mock_of(active.cancel_token).cancel.assert_called_once_with("User /stop command")
        _mock_of(active.task).cancel.assert_called_once()
        assert "telegram:user1" not in h._active_tasks

    @pytest.mark.asyncio
    async def test_skips_task_cancel_if_done(self) -> None:
        active = _make_active_task(done=True)
        h = _host(active_tasks={"telegram:user1": active})

        await _as_router(h)._abort_session_task("telegram:user1", reason="done")
        _mock_of(active.task).cancel.assert_not_called()

    @pytest.mark.asyncio
    async def test_clears_the_pending_approval(self) -> None:
        active = _make_active_task()
        h = _host(
            active_tasks={"telegram:user1": active},
            pending_approvals=waiting("telegram:user1", "msg-42"),
        )
        await _as_router(h)._abort_session_task("telegram:user1", reason="cleanup")
        assert "telegram:user1" not in h._pending_approvals

    @pytest.mark.asyncio
    async def test_cleans_up_placeholder(self) -> None:
        active = _make_active_task(placeholder_id="ph-1")
        h = _host(active_tasks={"telegram:user1": active})

        await _as_router(h)._abort_session_task(
            "telegram:user1",
            reason="test",
            placeholder_text="Stopped.",
        )

        h._fx.cleanup_placeholder.assert_awaited_once_with(
            "telegram",
            "user1",
            "ph-1",
            "Stopped.",
        )

    @pytest.mark.asyncio
    async def test_no_placeholder_cleanup_without_text(self) -> None:
        active = _make_active_task(placeholder_id="ph-1")
        h = _host(active_tasks={"telegram:user1": active})

        await _as_router(h)._abort_session_task("telegram:user1", reason="silent")
        h._fx.cleanup_placeholder.assert_not_awaited()


@pytest.fixture
def opened() -> Iterator[AsyncMock]:
    """The conversation a ``/new`` opens; stored for real in ``tests/core/channel_bridge/test_conversation_start.py``."""
    with patch("app.core.channel_bridge.command_session.start_conversation", AsyncMock()) as start:
        yield start


class TestHandleNewSession:
    """Integration tests for _handle_new_session (IR-03 /new cleanup)."""

    @pytest.mark.asyncio
    async def test_abort_and_clear_pending_on_active_task(self, opened: AsyncMock) -> None:
        active = _make_active_task(placeholder_id="ph-1")
        h = _host(
            active_tasks={"telegram:user1": active},
            session_yolo={"telegram:user1": (0.0, None)},
            session_personality={"telegram:user1": "sassy"},
        )
        h._gate.clear_pending_for_key.return_value = 3

        await _handle_new_session(h, _msg())

        _mock_of(active.cancel_token).cancel.assert_called_once()
        h._gate.clear_pending_for_key.assert_called_once_with("telegram:user1")
        assert "telegram:user1" not in h._active_tasks
        assert "telegram:user1" not in h._session_yolo
        assert "telegram:user1" not in h._session_personality
        h._bus.publish_outbound.assert_awaited()

    @pytest.mark.asyncio
    async def test_no_active_task_still_clears_pending(self, opened: AsyncMock) -> None:
        h = _host(
            session_yolo={"telegram:user1": (0.0, None)},
        )
        h._gate.clear_pending_for_key.return_value = 0

        await _handle_new_session(h, _msg())

        h._gate.clear_pending_for_key.assert_called_once_with("telegram:user1")
        assert "telegram:user1" not in h._session_yolo
        h._bus.publish_outbound.assert_awaited()

    @pytest.mark.asyncio
    async def test_the_new_conversation_opens_after_the_old_turn_is_gone(self, opened: AsyncMock) -> None:
        h = _host(active_tasks={"telegram:user1": _make_active_task()})
        running_when_opened: list[bool] = []

        def note_running(_: InboundMessage) -> None:
            running_when_opened.append("telegram:user1" in h._active_tasks)

        opened.side_effect = note_running
        msg = _msg()

        await _handle_new_session(h, msg)

        opened.assert_awaited_once_with(msg)
        assert running_when_opened == [False]

    @pytest.mark.asyncio
    async def test_a_conversation_that_could_not_be_opened_is_told_after_the_old_turn_was_stopped(
        self, opened: AsyncMock
    ) -> None:
        active = _make_active_task()
        h = _host(active_tasks={"telegram:user1": active})
        opened.side_effect = RuntimeError("db down")
        msg = _msg()

        await _handle_new_session(h, msg)

        _mock_of(active.cancel_token).cancel.assert_called_once()
        [reply] = h._bus.publish_outbound.await_args_list
        assert reply.args[0].content == get_text(msg, "new_session_failed")
