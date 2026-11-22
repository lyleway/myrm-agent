"""Tests for MessageBus registration, tracked sends, edits, inbound routing and lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.channels.core.bus import MessageBus
from app.channels.types import (
    ActionButton,
    ChannelCapabilities,
    InboundMessage,
    OutboundMessage,
)
from tests.channels.bus_testkit import EditRecordingChannel, FakeChannel, NoEditCapabilityChannel, make_out


class TestMessageBusRegister:
    def test_register_and_get_channel(self) -> None:
        bus = MessageBus()
        ch = FakeChannel()
        bus.register_channel(ch)
        assert bus.get_channel("fake") is ch
        assert "fake" in bus.registered_channels

    def test_register_replaces_existing(self) -> None:
        bus = MessageBus()
        ch1 = FakeChannel()
        ch2 = FakeChannel()
        bus.register_channel(ch1)
        bus.register_channel(ch2)
        assert bus.get_channel("fake") is ch2

    def test_get_unknown_returns_none(self) -> None:
        bus = MessageBus()
        assert bus.get_channel("nonexistent") is None


class TestMessageBusSendTracked:
    @pytest.mark.asyncio
    async def test_send_tracked_returns_message_id(self) -> None:
        bus = MessageBus()
        ch = FakeChannel()
        bus.register_channel(ch)
        msg = make_out()
        result = await bus.send_tracked(msg)
        assert result == "msg_id_1"
        assert len(ch.sent) == 1

    @pytest.mark.asyncio
    async def test_send_tracked_records_activity(self) -> None:
        bus = MessageBus()
        ch = FakeChannel()
        bus.register_channel(ch)
        assert ch.activity.last_outbound_at is None
        await bus.send_tracked(make_out())
        assert ch.activity.last_outbound_at is not None

    @pytest.mark.asyncio
    async def test_send_tracked_unknown_channel_returns_none(self) -> None:
        bus = MessageBus()
        msg = make_out(channel="unknown")
        result = await bus.send_tracked(msg)
        assert result is None

    @pytest.mark.asyncio
    async def test_send_tracked_downgrades_components(self) -> None:
        bus = MessageBus()
        ch = FakeChannel(caps=ChannelCapabilities(buttons=False))
        bus.register_channel(ch)
        btn = ActionButton(label="Click", action_id="test:click", url="https://example.com/click")
        msg = make_out(content="Do:", components=((btn,),))
        await bus.send_tracked(msg)
        assert len(ch.sent) == 1
        assert len(ch.sent[0].components) == 0
        assert "Click → https://example.com/click" in ch.sent[0].content


class TestMessageBusEditMessage:
    @pytest.mark.asyncio
    async def test_edit_returns_true_on_success(self) -> None:
        bus = MessageBus()
        ch = EditRecordingChannel()
        bus.register_channel(ch)
        result = await bus.edit_channel_message("fake", "chat1", "msg1", "new content")
        assert result is True
        assert ch.edits == [("chat1", "msg1", "new content")]

    @pytest.mark.asyncio
    async def test_edit_reports_false_without_edit_capability(self) -> None:
        bus = MessageBus()
        ch = NoEditCapabilityChannel()
        bus.register_channel(ch)
        result = await bus.edit_channel_message("fake", "chat1", "msg1", "new content")
        assert result is False
        assert ch.edits == []

    @pytest.mark.asyncio
    async def test_edit_returns_false_for_unknown_channel(self) -> None:
        bus = MessageBus()
        result = await bus.edit_channel_message("unknown", "chat1", "msg1", "content")
        assert result is False


class TestMessageBusInbound:
    @pytest.mark.asyncio
    async def test_inbound_handler_routes_to_queue(self) -> None:
        bus = MessageBus()
        ch = FakeChannel()
        bus.register_channel(ch)

        msg = InboundMessage(
            channel="fake",
            sender_id="u1",
            content="hi",
            chat_id="c1",
        )
        await bus._handle_inbound(msg)
        result = await asyncio.wait_for(bus.consume_inbound(), timeout=1.0)
        assert result.content == "hi"


class TestMessageBusLifecycle:
    @pytest.mark.asyncio
    async def test_start_and_stop(self) -> None:
        bus = MessageBus()
        await bus.start()
        assert bus._running is True
        assert bus._dispatch_task is not None
        await bus.stop()
        assert bus._running is False
        assert bus._dispatch_task is None

    @pytest.mark.asyncio
    async def test_start_idempotent(self) -> None:
        bus = MessageBus()
        await bus.start()
        task1 = bus._dispatch_task
        await bus.start()
        assert bus._dispatch_task is task1
        await bus.stop()

    @pytest.mark.asyncio
    async def test_dispatch_loop_sends_message(self) -> None:
        bus = MessageBus()
        ch = FakeChannel()
        bus.register_channel(ch)
        await bus.start()
        try:
            await bus.publish_outbound(make_out())
            await asyncio.sleep(0.1)
            assert len(ch.sent) == 1
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_dispatch_loop_survives_poisoned_message_and_records_failure(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A message that blows up mid-dispatch must not stop delivery for every channel.

        The failure is absorbed per message and recorded as a failed send, so the rest of the
        queue still goes out instead of the loop dying silently.
        """
        bus = MessageBus()
        ch = FakeChannel()
        bus.register_channel(ch)

        class _FlakyGate:
            async def evaluate_and_apply(self, msg: OutboundMessage) -> OutboundMessage:
                if "poison" in msg.content:
                    raise RuntimeError("poisoned message")
                return msg

        monkeypatch.setattr(
            "app.channels.core.outbound_dispatch.get_outbound_content_gate",
            lambda: _FlakyGate(),
        )
        recorded: list[OutboundMessage] = []

        async def _record(msg: OutboundMessage, error: str, *, retries_exhausted: bool) -> None:
            recorded.append(msg)

        monkeypatch.setattr(bus, "_record_outbound_failure", _record)

        await bus.start()
        try:
            await bus.publish_outbound(make_out(content="poison payload"))
            await bus.publish_outbound(make_out(content="healthy payload"))
            await asyncio.sleep(0.1)

            assert bus.is_dispatch_loop_alive()
            assert [m.content for m in recorded] == ["poison payload"]
            assert [m.content for m in ch.sent] == ["healthy payload"]
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_pre_delivery_failure_does_not_livelock_via_durable_recovery(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A deterministically-failing message must not be re-injected by disk recovery forever.

        Releasing the obligation instead of recording the failure leaves the disk record pending,
        so the idle-timeout recovery re-injects it every cycle. The failure must be recorded so
        the record is acked and the DLQ's bounded retries take over.
        """
        attempts = {"n": 0}

        class _AlwaysRaisingGate:
            async def evaluate_and_apply(self, msg: OutboundMessage) -> OutboundMessage:
                attempts["n"] += 1
                raise RuntimeError("deterministic pre-delivery failure")

        monkeypatch.setattr(
            "app.channels.core.outbound_dispatch.get_outbound_content_gate",
            lambda: _AlwaysRaisingGate(),
        )
        bus = MessageBus(dlq_dir=tmp_path)
        bus.register_channel(FakeChannel())
        await bus.start()
        try:
            await bus.publish_outbound(make_out(content="payload"))
            await asyncio.sleep(3.2)
            assert attempts["n"] == 1
            assert await bus._durable_outbound.count_pending() == 0
        finally:
            await bus.stop()
