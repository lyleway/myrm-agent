"""The ``/retry`` and ``/undo`` command handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.channels.routing.commands import (
    handle_retry,
    handle_undo,
)
from app.channels.types import OutboundMessage
from tests.channels.command_testkit import make_command_bus, make_command_msg, make_command_resolver


class TestHandleRetry:
    @pytest.mark.asyncio
    async def test_no_handler_replies(self) -> None:
        msg = make_command_msg(content="/retry")
        bus = make_command_bus()
        resolver = make_command_resolver()

        result = await handle_retry(msg, bus, resolver, retry_handler=None)

        assert result is None
        bus.publish_outbound.assert_called_once()
        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "not configured" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_retry_success_returns_msg(self) -> None:
        from app.channels.protocols.turn_management import RetryResult

        msg = make_command_msg(content="/retry")
        bus = make_command_bus()
        resolver = make_command_resolver()

        handler = AsyncMock(
            return_value=RetryResult(
                success=True,
                query="original question",
                deleted_count=2,
            )
        )

        result = await handle_retry(msg, bus, resolver, retry_handler=handler)

        assert result is not None
        assert result.content == "original question"
        handler.assert_awaited_once()
        bus.publish_outbound.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_success_publishes_revert_notice(self) -> None:
        from app.channels.protocols.turn_management import RetryResult

        msg = make_command_msg(content="/retry")
        bus = make_command_bus()
        resolver = make_command_resolver()

        handler = AsyncMock(
            return_value=RetryResult(
                success=True,
                query="original question",
                deleted_count=2,
                reverted_count=3,
                files_not_revertible=1,
            )
        )

        result = await handle_retry(msg, bus, resolver, retry_handler=handler)

        assert result is not None
        assert result.content == "original question"
        bus.publish_outbound.assert_called_once()
        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "3" in reply.content
        assert "1" in reply.content

    @pytest.mark.asyncio
    async def test_retry_nothing_to_delete(self) -> None:
        from app.channels.protocols.turn_management import RetryResult

        msg = make_command_msg(content="/retry")
        bus = make_command_bus()
        resolver = make_command_resolver()

        handler = AsyncMock(
            return_value=RetryResult(
                success=True,
                query="",
                deleted_count=0,
            )
        )

        result = await handle_retry(msg, bus, resolver, retry_handler=handler)

        assert result is None
        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "nothing" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_retry_exception(self) -> None:
        msg = make_command_msg(content="/retry")
        bus = make_command_bus()
        resolver = make_command_resolver()
        handler = AsyncMock(side_effect=RuntimeError("db error"))

        result = await handle_retry(msg, bus, resolver, retry_handler=handler)

        assert result is None
        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "failed" in reply.content.lower()


class TestHandleUndo:
    @pytest.mark.asyncio
    async def test_no_handler_replies(self) -> None:
        msg = make_command_msg(content="/undo")
        bus = make_command_bus()
        resolver = make_command_resolver()

        await handle_undo(msg, bus, resolver, undo_handler=None)

        bus.publish_outbound.assert_called_once()
        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "not configured" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_undo_success_message(self) -> None:
        from app.channels.protocols.turn_management import UndoResult

        msg = make_command_msg(content="/undo")
        bus = make_command_bus()
        resolver = make_command_resolver()

        handler = AsyncMock(
            return_value=UndoResult(
                success=True,
                deleted_count=3,
                reverted_count=0,
            )
        )

        await handle_undo(msg, bus, resolver, undo_handler=handler)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "3" in reply.content

    @pytest.mark.asyncio
    async def test_undo_with_reverted_files(self) -> None:
        from app.channels.protocols.turn_management import UndoResult

        msg = make_command_msg(content="/undo")
        bus = make_command_bus()
        resolver = make_command_resolver()

        handler = AsyncMock(
            return_value=UndoResult(
                success=True,
                deleted_count=2,
                reverted_count=4,
            )
        )

        await handle_undo(msg, bus, resolver, undo_handler=handler)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "2" in reply.content
        assert "4" in reply.content

    @pytest.mark.asyncio
    async def test_undo_only_not_revertible_files(self) -> None:
        from app.channels.protocols.turn_management import UndoResult

        msg = make_command_msg(content="/undo")
        bus = make_command_bus()
        resolver = make_command_resolver()

        handler = AsyncMock(
            return_value=UndoResult(
                success=True,
                deleted_count=2,
                reverted_count=0,
                files_not_revertible=3,
            )
        )

        await handle_undo(msg, bus, resolver, undo_handler=handler)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "2" in reply.content
        assert "3" in reply.content
        assert "revert" not in reply.content.lower().split("\n")[0]

    @pytest.mark.asyncio
    async def test_undo_zero_reverted_no_revert_line(self) -> None:
        from app.channels.protocols.turn_management import UndoResult

        msg = make_command_msg(content="/undo")
        bus = make_command_bus()
        resolver = make_command_resolver()

        handler = AsyncMock(
            return_value=UndoResult(
                success=True,
                deleted_count=2,
                reverted_count=0,
            )
        )

        await handle_undo(msg, bus, resolver, undo_handler=handler)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "revert" not in reply.content.lower()

    @pytest.mark.asyncio
    async def test_undo_nothing(self) -> None:
        from app.channels.protocols.turn_management import UndoResult

        msg = make_command_msg(content="/undo")
        bus = make_command_bus()
        resolver = make_command_resolver()

        handler = AsyncMock(
            return_value=UndoResult(
                success=True,
                deleted_count=0,
            )
        )

        await handle_undo(msg, bus, resolver, undo_handler=handler)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "nothing" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_undo_failure(self) -> None:
        from app.channels.protocols.turn_management import UndoResult

        msg = make_command_msg(content="/undo")
        bus = make_command_bus()
        resolver = make_command_resolver()

        handler = AsyncMock(return_value=UndoResult(success=False))

        await handle_undo(msg, bus, resolver, undo_handler=handler)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "failed" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_undo_exception(self) -> None:
        msg = make_command_msg(content="/undo")
        bus = make_command_bus()
        resolver = make_command_resolver()
        handler = AsyncMock(side_effect=RuntimeError("db error"))

        await handle_undo(msg, bus, resolver, undo_handler=handler)

        reply: OutboundMessage = bus.publish_outbound.call_args[0][0]
        assert "failed" in reply.content.lower()

    @pytest.mark.asyncio
    async def test_undo_group_resolves_user(self) -> None:
        from app.channels.protocols.turn_management import UndoResult

        msg = make_command_msg(content="/undo", is_group=True, chat_id="g1")
        bus = make_command_bus()
        resolver = make_command_resolver("group-user")

        handler = AsyncMock(
            return_value=UndoResult(
                success=True,
                deleted_count=1,
            )
        )

        await handle_undo(msg, bus, resolver, undo_handler=handler)

        resolver.resolve_group_user.assert_awaited_once()
        handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_undo_group_no_user_returns_early(self) -> None:
        msg = make_command_msg(content="/undo", is_group=True, chat_id="g1")
        bus = make_command_bus()
        resolver = MagicMock()
        resolver.resolve_group_user = AsyncMock(return_value=None)
        handler = AsyncMock()

        await handle_undo(msg, bus, resolver, undo_handler=handler)

        handler.assert_not_awaited()
