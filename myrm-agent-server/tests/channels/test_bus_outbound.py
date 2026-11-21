"""Tests for the outbound risk gate and control-plane egress routing on the MessageBus dispatch path."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.channels.core.bus import MessageBus
from tests.channels.bus_testkit import FakeChannel, make_out


class TestOutboundRiskGate:
    """Tests for apply_outbound_risk_gate in outbound_prepare.py."""

    def test_passthrough_when_no_rules(self) -> None:
        from unittest.mock import patch

        from app.channels.core.outbound_prepare import apply_outbound_risk_gate

        msg = make_out(content="secret phone 13812345678")
        with patch("app.services.risk.detection.get_detection_service") as mock_svc:
            mock_svc.return_value.rule_count = 0
            result = apply_outbound_risk_gate(msg)
        assert result is msg

    def test_passthrough_when_empty_content(self) -> None:
        from app.channels.core.outbound_prepare import apply_outbound_risk_gate

        msg = make_out(content="")
        result = apply_outbound_risk_gate(msg)
        assert result is msg

    def test_blocks_matching_content(self) -> None:
        from unittest.mock import patch

        from app.channels.core.outbound_prepare import apply_outbound_risk_gate
        from app.services.risk.detection import DetectionResult, RiskMatch

        msg = make_out(content="My phone is 13812345678")
        match = RiskMatch(
            rule_id="r1",
            display_name="CN Mobile",
            severity="high",
            category="pii",
            match_summary="chars:12-23",
        )
        blocked_result = DetectionResult(blocked=True, matches=(match,))

        with (
            patch("app.services.risk.detection.get_detection_service") as mock_svc,
            patch("app.channels.core.outbound_prepare.asyncio.ensure_future"),
        ):
            mock_svc.return_value.rule_count = 5
            mock_svc.return_value.detect.return_value = blocked_result
            result = apply_outbound_risk_gate(msg)

        assert result is not msg
        assert "13812345678" not in result.content
        assert result.channel == msg.channel
        assert result.recipient_id == msg.recipient_id

    def test_passthrough_when_no_match(self) -> None:
        from unittest.mock import patch

        from app.channels.core.outbound_prepare import apply_outbound_risk_gate
        from app.services.risk.detection import DetectionResult

        msg = make_out(content="Safe message without sensitive data")
        no_match = DetectionResult(blocked=False, matches=())

        with patch("app.services.risk.detection.get_detection_service") as mock_svc:
            mock_svc.return_value.rule_count = 5
            mock_svc.return_value.detect.return_value = no_match
            result = apply_outbound_risk_gate(msg)

        assert result is msg

    @pytest.mark.asyncio
    async def test_dispatch_loop_applies_risk_gate(self) -> None:
        """Integration: blocked message is replaced before reaching channel.send()."""
        from unittest.mock import patch

        from app.services.risk.detection import DetectionResult, RiskMatch

        match = RiskMatch(
            rule_id="r1",
            display_name="DB Credential",
            severity="critical",
            category="credential",
            match_summary="chars:0-42",
        )
        blocked_result = DetectionResult(blocked=True, matches=(match,))

        bus = MessageBus()
        ch = FakeChannel()
        bus.register_channel(ch)
        await bus.start()

        try:
            with patch("app.services.risk.detection.get_detection_service") as mock_svc:
                mock_svc.return_value.rule_count = 5
                mock_svc.return_value.detect.return_value = blocked_result
                await bus.publish_outbound(make_out(content="postgres://admin:secret@db.internal:5432/prod"))
                await asyncio.sleep(0.2)

            assert len(ch.sent) == 1
            assert "postgres://" not in ch.sent[0].content
            assert "secret" not in ch.sent[0].content
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_send_tracked_applies_risk_gate(self) -> None:
        """send_tracked also applies outbound risk gate."""
        from unittest.mock import patch

        from app.services.risk.detection import DetectionResult, RiskMatch

        match = RiskMatch(
            rule_id="r2",
            display_name="API Key",
            severity="critical",
            category="credential",
            match_summary="chars:0-48",
        )
        blocked_result = DetectionResult(blocked=True, matches=(match,))

        bus = MessageBus()
        ch = FakeChannel()
        bus.register_channel(ch)

        with patch("app.services.risk.detection.get_detection_service") as mock_svc:
            mock_svc.return_value.rule_count = 3
            mock_svc.return_value.detect.return_value = blocked_result
            msg_id = await bus.send_tracked(make_out(content="Your API key is sk-abc123def456"))

        assert msg_id is not None
        assert len(ch.sent) == 1
        assert "sk-abc123def456" not in ch.sent[0].content


class TestCpEgressDispatch:
    @pytest.mark.asyncio
    async def test_dispatch_uses_cp_egress_when_routed(self, tmp_path) -> None:
        from unittest.mock import patch

        bus = MessageBus(dlq_dir=tmp_path)
        ch = FakeChannel()
        bus.register_channel(ch)
        await bus.start()
        try:
            with (
                patch(
                    "app.services.channels.cp_egress_client.should_route_via_control_plane",
                    return_value=True,
                ),
                patch(
                    "app.services.channels.cp_egress_client.send_via_control_plane",
                    new_callable=AsyncMock,
                    return_value="cp-msg-1",
                ),
            ):
                await bus.publish_outbound(make_out(channel="feishu", content="via cp"))
                await asyncio.sleep(0.5)
            assert len(ch.sent) == 0
            from myrm_agent_harness.infra.delivery.storage import (
                load_pending_deliveries,
            )

            assert await load_pending_deliveries(base_dir=tmp_path) == []
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_dispatch_stopped_channel_retains_disk(self, tmp_path) -> None:
        from myrm_agent_harness.infra.delivery.storage import load_pending_deliveries

        from app.channels.types import ChannelStatus

        bus = MessageBus(dlq_dir=tmp_path)
        ch = FakeChannel()
        ch._status = ChannelStatus.STOPPED
        bus.register_channel(ch)
        await bus.start()
        try:
            await bus.publish_outbound(make_out(content="stopped retain"))
            await asyncio.sleep(0.4)
            assert len(ch.sent) == 0
            assert len(await load_pending_deliveries(base_dir=tmp_path)) == 1
        finally:
            await bus.stop()

    @pytest.mark.asyncio
    async def test_unregistered_channel_cp_egress_acks(self, tmp_path) -> None:
        from unittest.mock import patch

        bus = MessageBus(dlq_dir=tmp_path)
        await bus.start()
        try:
            with (
                patch(
                    "app.services.channels.cp_egress_client.should_route_via_control_plane",
                    return_value=True,
                ),
                patch(
                    "app.services.channels.cp_egress_client.send_via_control_plane",
                    new_callable=AsyncMock,
                    return_value="cp-msg-2",
                ),
            ):
                await bus.publish_outbound(make_out(channel="feishu", content="orphan cp"))
                await asyncio.sleep(0.5)
            assert await bus.durable_outbound.count_pending() == 0
        finally:
            await bus.stop()
