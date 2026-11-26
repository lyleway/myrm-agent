"""Tests for MessageBus edge cases: queue overflow, dead letters, permanent-failure callbacks, dispatch errors and channel unregistration."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.channels.core.bus import MessageBus
from app.channels.types import (
    InboundMessage,
    OutboundMessage,
)
from tests.channels.bus_testkit import EditRecordingChannel, FakeChannel, make_out


class TestMessageBusEdgeCases:
    @pytest.mark.asyncio
    async def test_outbound_queue_full_drops_message(self) -> None:
        bus = MessageBus(max_queue_size=1)
        bus._ensure_queues()
        bus._outbound.put_nowait(make_out())
        await bus.publish_outbound(make_out(content="overflow"))
        assert bus._outbound.qsize() == 1

    @pytest.mark.asyncio
    async def test_inbound_queue_full_drops_message(self) -> None:
        bus = MessageBus(max_queue_size=1)
        bus._ensure_queues()
        msg = InboundMessage(channel="fake", sender_id="u1", content="hi", chat_id="c1")
        bus._inbound.put_nowait(msg)
        await bus._handle_inbound(msg)
        assert bus._inbound.qsize() == 1

    @pytest.mark.asyncio
    async def test_send_tracked_disabled_channel_returns_none(self) -> None:
        from app.channels.types import ChannelStatus

        bus = MessageBus()
        ch = FakeChannel()
        bus.register_channel(ch)
        ch._status = ChannelStatus.DISABLED
        result = await bus.send_tracked(make_out())
        assert result is None

    @pytest.mark.asyncio
    async def test_send_tracked_stopped_channel_returns_none(self, tmp_path) -> None:
        from myrm_agent_harness.infra.delivery.storage import load_pending_deliveries

        from app.channels.types import ChannelStatus

        bus = MessageBus(dlq_dir=tmp_path)
        ch = FakeChannel()
        bus.register_channel(ch)
        ch._status = ChannelStatus.STOPPED
        result = await bus.send_tracked(make_out())
        assert result is None
        assert await load_pending_deliveries(base_dir=tmp_path) == []

    @pytest.mark.asyncio
    async def test_send_tracked_send_failure_returns_none(self) -> None:
        class FailSendChannel(FakeChannel):
            async def send(self, msg: OutboundMessage) -> str | None:
                raise RuntimeError("send failed")

        bus = MessageBus()
        ch = FailSendChannel()
        bus.register_channel(ch)
        result = await bus.send_tracked(make_out())
        assert result is None

    @pytest.mark.asyncio
    async def test_send_tracked_null_send_no_false_ack(self, tmp_path) -> None:
        class NullSendChannel(FakeChannel):
            async def send(self, msg: OutboundMessage) -> str | None:
                self.sent.append(msg)
                return None

        dlq_dir = tmp_path / "dlq"
        dlq_dir.mkdir()
        bus = MessageBus(dlq_dir=dlq_dir)
        bus.register_channel(NullSendChannel())
        await bus.start()
        try:
            result = await bus.send_tracked(make_out(content="null guard"))
            assert result is None
            from myrm_agent_harness.infra.delivery.storage import (
                load_pending_deliveries,
            )

            assert await load_pending_deliveries(base_dir=dlq_dir) == []
            dlq = await bus.get_dlq_messages()
            assert len(dlq) == 1
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_dispatch_null_send_moves_to_dlq(self, tmp_path) -> None:
        class NullSendChannel(FakeChannel):
            async def send(self, msg: OutboundMessage) -> str | None:
                self.sent.append(msg)
                return None

        dlq_dir = tmp_path / "dlq"
        dlq_dir.mkdir()
        bus = MessageBus(dlq_dir=dlq_dir)
        ch = NullSendChannel()
        bus.register_channel(ch)
        await bus.start()
        try:
            await bus.publish_outbound(make_out(content="dispatch null"))
            await asyncio.sleep(2.0)
            assert len(ch.sent) == 1
            from myrm_agent_harness.infra.delivery.storage import (
                load_pending_deliveries,
            )

            assert await load_pending_deliveries(base_dir=dlq_dir) == []
            dlq = await bus.get_dlq_messages()
            assert len(dlq) == 1
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_send_tracked_failure_invokes_permanent_failure_callback(self, tmp_path) -> None:
        class FailSendChannel(FakeChannel):
            async def send(self, msg: OutboundMessage) -> str | None:
                raise RuntimeError("send failed")

        callback = AsyncMock()
        dlq_dir = tmp_path / "dlq"
        dlq_dir.mkdir()
        bus = MessageBus(dlq_dir=dlq_dir, on_permanent_failure=callback)
        bus.register_channel(FailSendChannel())
        await bus.start()
        try:
            result = await bus.send_tracked(make_out())
            assert result is None
            callback.assert_awaited_once()
            assert callback.await_args.args[1] == "send failed"
            dlq_messages = await bus.get_dlq_messages()
            assert len(dlq_messages) == 1
            assert dlq_messages[0].channel == "fake"
            assert bus._dlq is not None
            await bus._dlq._process_failed_messages()
            callback.assert_awaited_once()
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_send_tracked_failure_skips_duplicate_callback_with_ledger(self, tmp_path) -> None:
        class FailSendChannel(FakeChannel):
            async def send(self, msg: OutboundMessage) -> str | None:
                raise RuntimeError("send failed")

        callback = AsyncMock()
        dlq_dir = tmp_path / "dlq"
        dlq_dir.mkdir()
        ledger_path = tmp_path / "ledger.db"
        from app.channels.reliability.delivery_notify_ledger import (
            SqliteDeliveryNotifyLedger,
        )

        ledger = SqliteDeliveryNotifyLedger(ledger_path)
        bus = MessageBus(
            dlq_dir=dlq_dir,
            on_permanent_failure=callback,
            notification_ledger=ledger,
        )
        bus.register_channel(FailSendChannel())
        await bus.start()
        try:
            await bus.send_tracked(make_out())
            callback.assert_awaited_once()
            callback.reset_mock()

            await bus._dlq._process_failed_messages()
            callback.assert_not_awaited()

            bus2 = MessageBus(
                dlq_dir=dlq_dir,
                on_permanent_failure=callback,
                notification_ledger=SqliteDeliveryNotifyLedger(ledger_path),
            )
            bus2.register_channel(FailSendChannel())
            await bus2.start()
            try:
                await bus2._dlq._process_failed_messages()
                callback.assert_not_awaited()
            finally:
                await bus2.stop()
        finally:
            await bus.stop()
            ledger.close()

    @pytest.mark.asyncio
    async def test_send_tracked_failure_presync_notified_merged_on_start(self, tmp_path) -> None:
        class FailSendChannel(FakeChannel):
            async def send(self, msg: OutboundMessage) -> str | None:
                raise RuntimeError("send failed")

        callback = AsyncMock()
        dlq_dir = tmp_path / "dlq"
        dlq_dir.mkdir()
        bus = MessageBus(dlq_dir=dlq_dir, on_permanent_failure=callback)
        bus.register_channel(FailSendChannel())
        await bus.send_tracked(make_out())
        assert len(bus._presync_notified_delivery_ids) == 1
        await bus.start()
        try:
            assert bus._presync_notified_delivery_ids == set()
            assert bus._dlq is not None
            await bus._dlq._process_failed_messages()
            callback.assert_awaited_once()
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_record_outbound_failure_callback_without_dlq_dir(self) -> None:
        callback = AsyncMock()
        bus = MessageBus(on_permanent_failure=callback)
        await bus._record_outbound_failure(make_out(), "send failed", retries_exhausted=True)
        callback.assert_awaited_once()
        assert callback.await_args.args[1] == "send failed"

    @pytest.mark.asyncio
    async def test_record_outbound_failure_callback_exception_does_not_raise(self, tmp_path) -> None:
        async def failing_callback(_delivery: object, _error: str) -> None:
            raise RuntimeError("callback failed")

        dlq_dir = tmp_path / "dlq"
        dlq_dir.mkdir()
        bus = MessageBus(dlq_dir=dlq_dir, on_permanent_failure=failing_callback)
        await bus._record_outbound_failure(make_out(), "send failed", retries_exhausted=True)

    @pytest.mark.asyncio
    async def test_edit_message_exception_returns_false(self) -> None:
        class FailEditChannel(EditRecordingChannel):
            async def edit_message(self, chat_id: str, message_id: str, content: str) -> None:
                raise RuntimeError("edit failed")

        bus = MessageBus()
        ch = FailEditChannel()
        bus.register_channel(ch)
        result = await bus.edit_channel_message("fake", "c1", "m1", "new")
        assert result is False

    @pytest.mark.asyncio
    async def test_dispatch_to_unknown_channel_drops(self) -> None:
        bus = MessageBus()
        ch = FakeChannel()
        bus.register_channel(ch)
        await bus.start()
        try:
            await bus.publish_outbound(make_out(channel="nonexistent"))
            await asyncio.sleep(0.15)
            assert len(ch.sent) == 0
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_dispatch_send_failure_records_error(self) -> None:
        class FailDispatchChannel(FakeChannel):
            async def send(self, msg: OutboundMessage) -> str | None:
                raise RuntimeError("dispatch fail")

        bus = MessageBus()
        ch = FailDispatchChannel()
        bus.register_channel(ch)
        await bus.start()
        try:
            await bus.publish_outbound(make_out())
            await asyncio.sleep(0.5)
            assert ch.health.consecutive_failures > 0
        finally:
            await bus.stop()

    def test_unregister_channel(self) -> None:
        bus = MessageBus()
        ch = FakeChannel()
        bus.register_channel(ch)
        assert bus.get_channel("fake") is ch
        removed = bus.unregister_channel("fake")
        assert removed is ch
        assert bus.get_channel("fake") is None

    def test_unregister_unknown_returns_none(self) -> None:
        bus = MessageBus()
        removed = bus.unregister_channel("nonexistent")
        assert removed is None
